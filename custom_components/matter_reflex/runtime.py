"""Maintain the HA lease independently of slow subscriptions and ACL operations."""

import asyncio
import logging
from datetime import UTC, datetime

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
from .compiler import async_compile
from .const import DOMAIN, HEARTBEAT_SECONDS, LEASE_SECONDS
from .transport import CHANNEL_ERRORS, Channel, ReflexError, client_for, is_reflex
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
        if mode not in ("matter", "http"):
            raise ReflexError("Unsupported Reflex rules transport")
        self.channel = (
            Channel(hass, self.node, entry.data["key"]) if mode == "http" else None
        )
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
        self.target_hash = None
        self.last_sync = None
        self.acl_synced = False

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
        for target in (self._lease_loop, self._status_loop, self._sync_loop):
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
            translation_key="rule" if rule else "sync",
            translation_placeholders={"detail": detail},
        )

    def clear_issue(self, key):
        ir.async_delete_issue(self.hass, DOMAIN, self.entry.entry_id + "." + key)

    async def _lease_loop(self):
        while True:
            try:
                if self.hass.state is CoreState.running:
                    async with asyncio.timeout(4):
                        client = self.client()
                        if not is_reflex(client.get_node(self.node)):
                            raise ReflexError("Selected node has no Reflex endpoint")
                        await native.renew(client, self.node, LEASE_SECONDS)
            except CHANNEL_ERRORS as err:
                _LOGGER.debug("Lease unavailable: %s", type(err).__name__)
            await asyncio.sleep(HEARTBEAT_SECONDS)

    async def read_status(self, client):
        if client is not None:
            try:
                async with asyncio.timeout(3):
                    return await native.read_status(client, self.node)
            except CHANNEL_ERRORS:
                if self.channel is None:
                    raise
        if self.channel is None:
            raise ReflexError("Matter Server is unavailable")
        return await self.channel.status(client)

    async def transfer(self, client, image):
        if self.channel is not None:
            await self.channel.request(client, "rules", image)
            return
        for frame in native.frames(image):
            await native.write_frame(client, self.node, frame)

    async def _status_loop(self):
        while True:
            try:
                try:
                    client = self.client()
                except ReflexError:
                    client = None
                async with asyncio.timeout(6):
                    latest = await self.read_status(client)
                if self.target_hash is not None and latest["hash"] != self.target_hash:
                    self.dirty.set()
            except CHANNEL_ERRORS as err:
                _LOGGER.debug("Reflex status unavailable: %s", type(err).__name__)
                latest = None
            if latest != self.status:
                self.status = latest
                self.notify()
            await asyncio.sleep(3)

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
                await asyncio.sleep(10)
            self.notify()

    async def sync(self):
        if self.hass.state is not CoreState.running:
            return
        client = self.client()
        if not is_reflex(client.get_node(self.node)):
            raise ReflexError("The selected Matter node has no Reflex endpoint")
        compiled = await async_compile(
            self.hass, self.entry.data["matter_entry_id"], self.node
        )
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
        previously_verified = self.acl_synced and self.target_hash == expected
        self.target_hash = expected
        observed = await self.read_status(client)
        if (
            previously_verified
            and observed["hash"] == expected
            and self.last_sync
            and (datetime.now(UTC) - self.last_sync).total_seconds() < 3600
        ):
            return
        await self.acls.reconcile(client, compiled.permissions, prune=False)
        if observed["hash"] != self.target_hash:
            await self.transfer(client, compiled.image)
            observed = await self.read_status(client)
        if observed["hash"] != self.target_hash:
            raise ReflexError("Device did not commit the expected rule hash")
        # Only remove obsolete permissions once the device confirms the rules.
        await self.acls.reconcile(client, compiled.permissions, prune=True)
        self.status = observed
        self.last_sync = datetime.now(UTC)
        self.acl_synced = True
        self.clear_issue("sync")
