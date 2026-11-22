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

# Older servers may only provide these vendor values through HTTP, or expose
# them for reads without forwarding changes. Bound the staleness on that path.
UNCONFIRMED_INTERVAL = 300
RETRY_INTERVAL = 60


class StatusMonitor:
    def __init__(self, runtime):
        self.runtime = runtime
        self.client = None
        self.online = False
        self.bound_node = None
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
        if self.client is client and self.bound_node is node:
            return
        self.detach()
        self.client = client
        if client is not None:
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
        self.runtime.acl_synced = False
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
    def attribute(self, path, event, value):
        if not self.online:
            return
        # subscribe_events reports a single value; node_updated may also carry
        # old cached fields. Never replace a newer HTTP snapshot with that cache.
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
            self.publish({**status, key: value})

    async def refresh(self, client):
        async with self.lock:
            if client is not None and client is not self.client:
                raise ReflexError("Matter client changed before status refresh")
            previously_confirmed = self.confirmed
            generation, sequence = self.generation, self.sequence
            async with asyncio.timeout(10):
                latest = await self.runtime.read_status(client)
            if generation != self.generation:
                raise ReflexError("Matter connection changed during status refresh")
            # A newer report can arrive while the HTTP/IM read is in flight.
            latest = {
                **latest,
                **{
                    key: value
                    for key, (index, value) in self.changes.items()
                    if index > sequence
                },
            }
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

    async def run(self):
        force = True
        while True:
            self.wake.clear()
            route = self.online or (
                self.runtime.channel is not None and not self.removed
            )
            delay = None
            if route and (force or not self.confirmed):
                try:
                    latest = await self.refresh(self.client if self.online else None)
                except CHANNEL_ERRORS:
                    if not self.wake.is_set():
                        self.publish(None)
                    delay = RETRY_INTERVAL
                else:
                    if not self.confirmed:
                        delay = UNCONFIRMED_INTERVAL
                    # An immediate authenticated HTTP read during an outage can
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
