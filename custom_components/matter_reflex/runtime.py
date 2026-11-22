"""Maintain the HA lease independently of slow subscriptions and ACL operations."""

import asyncio
import logging
from datetime import UTC, datetime
from time import monotonic

from homeassistant.components.automation.const import EVENT_AUTOMATION_RELOADED
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import CoreState, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import EVENT_DEVICE_REGISTRY_UPDATED
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_registry import EVENT_ENTITY_REGISTRY_UPDATED
from homeassistant.helpers.label_registry import EVENT_LABEL_REGISTRY_UPDATED
from homeassistant.helpers.storage import Store

from . import native
from .acl import AclManager
from .activity import Activity
from .compiler import async_compile
from .const import DOMAIN, HEARTBEAT_SECONDS
from .ownership import lease_for
from .status import RETRY_INTERVAL, StatusMonitor
from .transport import (
    CHANNEL_ERRORS,
    Channel,
    ESPHomeChannel,
    ReflexError,
    client_for,
    is_reflex,
)
from .wire import digest

_LOGGER = logging.getLogger(__name__)


class Runtime:
    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        self.node = entry.data["node_id"]
        # Entries created before transport negotiation used the authenticated
        # Thread channel for rules and have a key but no mode field.
        mode = entry.data.get("mode", "http" if "key" in entry.data else "matter")
        if mode == "matter":
            self.channel = None
        elif mode == "http":
            self.channel = Channel(hass, self.node, entry.data["key"])
        elif mode == "esphome":
            self.channel = ESPHomeChannel(
                hass, self.node, entry.data["fabric"], entry.data["esphome_entry_id"]
            )
        else:
            raise ReflexError("Unsupported Reflex rules transport")
        self.acls = AclManager(
            Store(
                hass,
                1,
                f"{DOMAIN}.{entry.entry_id}.acl",
                private=True,
                atomic_writes=True,
            ),
            self.node,
        )
        self.signal = f"{DOMAIN}_{entry.entry_id}"
        self.dirty = asyncio.Event()
        self.dirty.set()
        self.status = None
        self.native_status_readable = False
        self.monitor = StatusMonitor(self)
        self.activity = Activity(self)
        self.lease = lease_for(hass, entry.entry_id)
        self.desired_rules = {}
        self.direct_mask = 0
        self.target_hash = None
        self.last_sync = None
        self.acl_synced = False
        self.synced_permissions = None

    @property
    def rules_synchronized(self) -> bool | None:
        """Return whether the device and ACLs match the compiled rules."""
        if self.status is None:
            return None
        return self.acl_synced and self.status["hash"] == self.target_hash

    def esp_handles(self, entity_id, source):
        installed = self.activity.image_hash
        if installed is None or installed != self.monitor.ready_hash:
            return False
        # Compare each requested row with the last accepted image. An unrelated
        # failed transfer must not bring HA back into a working direct reflex.
        desired = {
            key: row
            for key, row in self.desired_rules.items()
            if row.direct
            and row.entity_id == entity_id
            and (source is None or source == row.source_entity)
        }
        ready = {
            key: self.activity.rules[key]
            for bit, key in enumerate(sorted(self.activity.rules))
            if self.monitor.ready_mask & (1 << bit)
        }
        return bool(desired) and all(
            ready.get(key) == row for key, row in desired.items()
        )

    def client(self):
        return client_for(
            self.hass, self.entry.data["matter_entry_id"], self.entry.data["fabric"]
        )

    def notify(self):
        async_dispatcher_send(self.hass, self.signal)

    async def start(self):
        await self.acls.load()
        for event in (
            EVENT_AUTOMATION_RELOADED,
            EVENT_ENTITY_REGISTRY_UPDATED,
            EVENT_DEVICE_REGISTRY_UPDATED,
            EVENT_LABEL_REGISTRY_UPDATED,
        ):
            self.entry.async_on_unload(self.hass.bus.async_listen(event, self._dirty))
        self.entry.async_on_unload(
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._state)
        )
        self.monitor.start()
        for target in (
            self._lease_loop,
            self._status_loop,
            self._sync_loop,
            self.activity.run,
        ):
            self.entry.async_create_background_task(
                self.hass, self._maintain_task(target), target.__name__
            )

    async def _maintain_task(self, worker):
        """Reconnect after Matter cancels an in-flight command on disconnect."""
        while True:
            try:
                await worker()
                return
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise  # Config entry unloaded or HA is shutting down.
                _LOGGER.debug(
                    "Matter cancelled a pending request in %s", worker.__name__
                )
                if worker == self._status_loop:
                    self.status = None
                    self.notify()
                elif worker == self._sync_loop:
                    self.dirty.set()
                await asyncio.sleep(1)

    @callback
    def _dirty(self, event):
        self.dirty.set()

    @callback
    def _state(self, event):
        if event.data["entity_id"].startswith("automation."):
            before, after = event.data.get("old_state"), event.data.get("new_state")
            if before is None or after is None or before.state != after.state:
                self.dirty.set()

    def issue(self, key, detail, rule=False):
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self.entry.entry_id + "." + key,
            config_entry_id=self.entry.entry_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="rule" if rule else key,
            translation_placeholders={"detail": detail},
        )

    def clear_issue(self, key):
        ir.async_delete_issue(self.hass, DOMAIN, self.entry.entry_id + "." + key)

    async def _lease_loop(self):
        clear_repair = True
        verified = None
        use_action = False
        next_tick = None
        while True:
            started = monotonic()
            try:
                if self.hass.state is CoreState.running:
                    async with asyncio.timeout(4):
                        client = self.client()
                        node = client.get_node(self.node)
                        if not is_reflex(node):
                            raise ReflexError("Selected node has no Reflex endpoint")
                        # On an upgrade/reconnect, rediscover the transport. Old boards
                        # reject InstantAction, so a stale choice also fails visibly.
                        current = (
                            client,
                            node.node_data.attributes.get(native.SOFTWARE_VERSION_PATH),
                        )
                        if current != verified:
                            use_action = await native.supports_lease_action(
                                client, self.node
                            )
                            verified = current
                        started = monotonic()  # Discovery can delay dispatch.
                        await native.renew(client, self.node, use_action)
                        self.lease.renew()
                        if clear_repair:
                            self.clear_issue("lease")
                            clear_repair = False
            except native.LeaseUnsupported as err:
                self.issue("lease", str(err))
                clear_repair = True
                verified = None
                await asyncio.sleep(60)
                next_tick = None
                continue
            except (*CHANNEL_ERRORS, asyncio.CancelledError) as err:
                if (
                    isinstance(err, asyncio.CancelledError)
                    and asyncio.current_task().cancelling()
                ):
                    raise
                verified = None
                _LOGGER.debug("Lease unavailable: %s", type(err).__name__)
            # A suspended event loop can miss several slots. Resume once,
            # without replaying a backlog of renewals.
            if next_tick is None or started >= next_tick + HEARTBEAT_SECONDS:
                next_tick = started
            next_tick += HEARTBEAT_SECONDS
            await asyncio.sleep(max(0.0, next_tick - monotonic()))

    async def read_status(self, client):
        if client is not None:
            try:
                async with asyncio.timeout(3):
                    latest = await native.read_status(
                        client, self.node, include_ready=bool(self.direct_mask)
                    )
            except CHANNEL_ERRORS:
                self.native_status_readable = False
                if self.channel is None:
                    raise
            else:
                self.native_status_readable = True
                if not self.direct_mask or "ready" in latest or self.channel is None:
                    return latest
                # Some Matter servers only expose the original vendor attributes.
                try:
                    return await self.channel.status(client)
                except CHANNEL_ERRORS:
                    return latest
        if self.channel is None:
            raise ReflexError("Matter Server is unavailable")
        return await self.channel.status(client)

    async def transfer(self, client, image):
        if self.channel is not None:
            await self.channel.send_rules(client, image)
            return
        for frame in native.frames(image):
            await native.write_frame(client, self.node, frame)

    async def _status_loop(self):
        await self.monitor.run()

    async def _sync_loop(self):
        while True:
            # Registry events give prompt updates. Periodic recompilation also
            # catches replaced nodes and recovers from a disconnected server.
            try:
                async with asyncio.timeout(60):
                    await self.dirty.wait()
            except TimeoutError:
                pass
            self.dirty.clear()
            if self.hass.state is not CoreState.running:
                self.dirty.set()
                await asyncio.sleep(2)
                continue
            if not self.monitor.online:
                # The monitor wakes synchronization when the board reconnects.
                continue
            try:
                # Leave room for ICD check-in, but recover from a stalled request.
                async with asyncio.timeout(120):
                    await self.sync()
            except Exception as err:
                self.acl_synced = False
                self.issue(
                    "sync",
                    str(err)
                    if isinstance(err, ReflexError)
                    else f"Synchronization failed ({type(err).__name__})",
                )
                _LOGGER.debug("Synchronization failed", exc_info=True)
                self.dirty.set()
                # Retry after sleeping even when many registry changes arrive.
                await asyncio.sleep(RETRY_INTERVAL)
            self.notify()

    async def sync(self):
        if self.hass.state is not CoreState.running:
            return
        client = self.client()
        node = client.get_node(self.node)
        if not node.available:
            raise ReflexError("The Reflex node is offline")
        if not is_reflex(node):
            raise ReflexError("The selected Matter node has no Reflex endpoint")
        compiled = await async_compile(
            self.hass,
            self.entry.data["matter_entry_id"],
            self.node,
            self.entry.entry_id,
        )
        self.desired_rules = compiled.activity
        direct_mask = sum(
            1 << bit
            for bit, key in enumerate(sorted(compiled.activity))
            if compiled.activity[key].direct
        )
        if self.direct_mask != direct_mask:
            self.direct_mask = direct_mask
            self.monitor.wake.set()
        current = set()
        for rule, reason in compiled.issues.items():
            key = f"rule.{rule}"
            current.add(self.entry.entry_id + "." + key)
            self.issue(key, f"{rule}: {reason}", rule=True)
        registry = ir.async_get(self.hass)
        prefix = self.entry.entry_id + ".rule."
        for domain, key in list(registry.issues):
            if domain == DOMAIN and key.startswith(prefix) and key not in current:
                ir.async_delete_issue(self.hass, DOMAIN, key)
        expected = digest(compiled.image)
        previously_verified = (
            self.acl_synced
            and self.target_hash == expected
            and self.synced_permissions == compiled.permissions
        )
        self.target_hash = expected
        # A periodic recompile only compares local state. Read back from the
        # peer when bootstrapping, changing rules/permissions or confirming a transfer.
        observed = self.status
        if not previously_verified or observed is None or observed["hash"] != expected:
            observed = await self.monitor.refresh(client)
        if (
            previously_verified
            and observed["hash"] == expected
            and self.last_sync
            and (datetime.now(UTC) - self.last_sync).total_seconds() < 3600
        ):
            self.activity.configure(expected, compiled.activity)
            return
        await self.acls.reconcile(client, compiled.permissions, prune=False)
        if observed["hash"] != self.target_hash:
            await self.transfer(client, compiled.image)
            observed = await self.monitor.refresh(client)
        if observed["hash"] != self.target_hash:
            raise ReflexError("Device did not commit the expected rule hash")
        self.activity.configure(expected, compiled.activity)
        # Only remove obsolete permissions once the device confirms the rules.
        await self.acls.reconcile(client, compiled.permissions, prune=True)
        self.last_sync = datetime.now(UTC)
        self.synced_permissions = compiled.permissions
        self.acl_synced = True
        self.clear_issue("sync")
