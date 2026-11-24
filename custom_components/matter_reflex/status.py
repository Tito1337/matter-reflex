"""Observe the existing Matter Server subscription for the selected Reflex node."""

import asyncio
from datetime import timedelta
from functools import partial

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval
from matter_server.common.models import EventType

from . import native
from .const import LEASE_SECONDS
from .transport import CHANNEL_ERRORS, ReflexError, is_reflex

# Older servers may only provide these vendor values through ESPHome, or expose
# them for reads without forwarding changes. Bound the staleness on that path.
UNCONFIRMED_INTERVAL = 300
RETRY_INTERVAL = 60


class StatusMonitor:
    def __init__(self, runtime):
        self.runtime = runtime
        self.client = None
        self.online = False
        self.ready_reported = False
        self.last_ready_report = (0, None)
        self.ready_hash = None
        self.ready_mask = 0
        self.mask_known = False
        self.restored_ready = None
        self.inflight_mask = 0xFFFFFFFF
        self.bound_node = None
        self.software_version = None
        self.removed = False
        self.reported_fields = set()
        self.check_expiration = False
        self.generation = 0
        self.sequence = 0
        self.changes = {}
        self.wake = asyncio.Event()
        self.lock = asyncio.Lock()
        self.unsubscribe = []

    def start(self):
        rt = self.runtime
        matter_entry = rt.hass.config_entries.async_get_entry(
            rt.entry.data["matter_entry_id"]
        )
        rt.entry.async_on_unload(matter_entry.async_on_state_change(self.bind))
        # A lost socket need not deliver SERVER_SHUTDOWN. start_listening()
        # also reconstructs the node map without emitting NODE_UPDATED.
        rt.entry.async_on_unload(
            async_track_time_interval(rt.hass, self.bind, timedelta(seconds=5))
        )
        rt.entry.async_on_unload(self.detach)
        self.bind()

    @callback
    def detach(self):
        for remove in self.unsubscribe:
            remove()
        self.unsubscribe.clear()
        self.generation += 1
        self.online = False
        self.bound_node = None
        self.client = None

    @callback
    def bind(self, _now=None):
        # Inspect local SDK state only; this callback sends no Matter request.
        try:
            client = self.runtime.client()
            if not client.connection.connected:
                client = None
        except ReflexError:
            client = None
        try:
            node = client.get_node(self.runtime.node) if client else None
        except CHANNEL_ERRORS:
            node = None
        version = (
            node.node_data.attributes.get(native.SOFTWARE_VERSION_PATH)
            if node
            else None
        )
        if version != self.software_version:
            self.software_version = version
            self.runtime.activity.wake.set()
        if self.client is client and self.bound_node is node:
            return
        self.detach()
        self.client = client
        if client is not None:
            self.unsubscribe.append(
                client.subscribe_events(
                    self.readiness,
                    EventType.ATTRIBUTE_UPDATED,
                    self.runtime.node,
                    native.READY_PATH,
                )
            )
            for path in native.STATUS_PATHS:
                self.unsubscribe.append(
                    client.subscribe_events(
                        partial(self.attribute, path),
                        EventType.ATTRIBUTE_UPDATED,
                        self.runtime.node,
                        path,
                    )
                )
            for event in (
                EventType.NODE_ADDED,
                EventType.NODE_UPDATED,
                EventType.NODE_REMOVED,
            ):
                self.unsubscribe.append(
                    client.subscribe_events(self.node_event, event, self.runtime.node)
                )
            self.unsubscribe.append(
                client.subscribe_events(self.shutdown, EventType.SERVER_SHUTDOWN)
            )
        self.bound_node = node
        self.reset(
            bool(node and node.available and is_reflex(node)),
            removed=(node is None if client is not None else self.removed),
        )

    def reset(self, online, removed=False):
        self.generation += 1
        self.online, self.removed = online, removed
        self.changes.clear()
        self.check_expiration = not online and not removed
        self.reported_fields.clear()
        self.ready_reported = False
        self.runtime.acl_synced = False
        self.runtime.activity.wake.set()
        self.publish(None)
        self.wake.set()
        if online:
            self.runtime.dirty.set()

    @callback
    def shutdown(self, event, data):
        self.reset(False)

    @callback
    def node_event(self, event, data):
        removed = event == EventType.NODE_REMOVED
        self.bound_node = None if removed else data
        online = not removed and data.available and is_reflex(data)
        if (online, removed) != (self.online, self.removed):
            self.reset(online, removed)

    def publish(self, status):
        rt = self.runtime
        if status is not None:
            # Keep the last verified ownership through a transient disconnect.
            # The reciprocal lease independently bounds how long HA will yield.
            if self.ready_hash != status["hash"]:
                self.ready_hash = status["hash"]
                self.ready_mask = 0
                self.mask_known = False
                if self.restored_ready is not None:
                    saved_hash, mask = self.restored_ready
                    if saved_hash == self.ready_hash:
                        self.ready_mask, self.mask_known = mask, True
                    self.restored_ready = None
            if "ready" in status:
                self.ready_mask = status["ready"]
                self.mask_known = True
            rt.observe_image(self.ready_hash)
            if self.mask_known and rt.direct_mask:
                rt.images.remember_ready(self.ready_hash, self.ready_mask)
        if status != rt.status:
            if (
                status is None
                or rt.status is None
                or status["executions"] != rt.status["executions"]
            ):
                rt.activity.wake.set()
            rt.status = status
            rt.notify()
            if (
                status is not None
                and rt.target_hash is not None
                and status["hash"] != rt.target_hash
            ):
                rt.dirty.set()

    @callback
    def readiness(self, event, value):
        # Re-read the mask with its image hash over the configured channel.
        if not self.online or type(value) is not int or not 0 <= value <= 0xFFFFFFFF:
            return
        self.last_ready_report = (self.last_ready_report[0] + 1, value)
        if not self.lock.locked():
            self.ready_reported = True
        status = self.runtime.status
        self.ready_mask &= value
        if self.restored_ready is not None:
            saved_hash, mask = self.restored_ready
            self.restored_ready = (saved_hash, mask & value)
        if value == 0 and self.ready_hash is not None:
            self.mask_known = True
        if self.mask_known and self.runtime.direct_mask:
            self.runtime.images.remember_ready(self.ready_hash, self.ready_mask)
        self.inflight_mask &= value
        changed = status is not None and value != status.get("ready")
        if (
            status is not None
            and "ready" in status
            and status["ready"] != self.ready_mask
        ):
            self.publish({**status, "ready": self.ready_mask})
        if self.runtime.direct_mask and (changed or status is None):
            self.wake.set()

    @callback
    def attribute(self, path, event, value):
        if not self.online:
            return
        # subscribe_events reports a single value; node_updated may also carry
        # old cached fields. Never replace a newer API snapshot with that cache.
        key = native.STATUS_PATHS[path]
        try:
            native.validate_status_value(key, value)
        except ReflexError:
            self.reported_fields.discard(key)
            self.changes.pop(key, None)
            if self.runtime.status is not None:
                self.publish(None)
                self.wake.set()
            return
        self.sequence += 1
        self.changes[key] = (self.sequence, value)
        if not self.lock.locked():
            self.reported_fields.add(key)
        status = self.runtime.status
        if status is not None:
            updated = {**status, key: value}
            if key == "hash" and value != status["hash"]:
                updated.pop("ready", None)
                if self.runtime.direct_mask:
                    self.wake.set()
            self.publish(updated)

    async def refresh(self, client):
        async with self.lock:
            if client is not None and client is not self.client:
                raise ReflexError("Matter client changed before status refresh")
            previously_confirmed = self.confirmed
            self.inflight_mask = 0xFFFFFFFF
            ready_sequence = self.last_ready_report[0]
            generation, sequence = self.generation, self.sequence
            async with asyncio.timeout(10):
                latest = await self.runtime.read_status(client)
            if generation != self.generation:
                raise ReflexError("Matter connection changed during status refresh")
            # A newer report can arrive while the API/IM read is in flight.
            read_hash = latest["hash"]
            latest = {
                **latest,
                **{
                    key: value
                    for key, (index, value) in self.changes.items()
                    if index > sequence
                },
            }
            sequence_now, reported_ready = self.last_ready_report
            if latest["hash"] != read_hash or (
                ready_sequence != sequence_now
                and "ready" in latest
                and (
                    latest["ready"] != reported_ready
                    or latest["ready"] & ~self.inflight_mask
                )
            ):
                latest.pop("ready", None)
                self.wake.set()
            self.publish(latest)
            if previously_confirmed and not self.confirmed:
                self.wake.set()
            return latest

    @property
    def confirmed(self):
        rt = self.runtime
        return (
            self.online
            and self.reported_fields.issuperset(native.STATUS_PATHS.values())
            and rt.native_status_readable
            and rt.status is not None
        )

    def readiness_poll(self):
        rt = self.runtime
        if (
            not self.online
            or not rt.direct_mask
            or rt.status is None
            # A readiness report still needs a read pairing the mask with its image.
            or (self.ready_reported and "ready" in rt.status)
        ):
            return None
        return 4 if self.ready_mask != rt.direct_mask else 30

    async def run(self):
        force = True
        while True:
            self.wake.clear()
            route = self.online or (
                self.runtime.channel is not None and not self.removed
            )
            delay = None
            if route and (force or not self.confirmed or self.readiness_poll()):
                try:
                    latest = await self.refresh(self.client if self.online else None)
                except CHANNEL_ERRORS:
                    if not self.wake.is_set():
                        self.publish(None)
                    delay = RETRY_INTERVAL
                else:
                    if not self.confirmed:
                        delay = UNCONFIRMED_INTERVAL
                    readiness_delay = self.readiness_poll()
                    if readiness_delay is not None:
                        delay = (
                            min(delay, readiness_delay) if delay else readiness_delay
                        )
                    # An immediate encrypted ESPHome read during an outage can
                    # still see the final HA lease. Observe its expiry once.
                    if self.check_expiration:
                        self.check_expiration = False
                        if not latest["active"]:
                            delay = LEASE_SECONDS + 1
            try:
                async with asyncio.timeout(delay):
                    await self.wake.wait()
                force = True
            except TimeoutError:
                # If an autonomous vendor report arrived while the timer ran,
                # skip the verification read and await lifecycle events instead.
                force = False
