"""Report completed ESP runs in HA Activity, grouped across target commands."""

import asyncio
import re
from collections import deque

from homeassistant.components.logbook.helpers import async_log_entry
from homeassistant.helpers.storage import Store

from . import native
from .const import DOMAIN
from .transport import CHANNEL_ERRORS, JournalUnsupported, ReflexError

RESULTS = {
    0: "accepted",
    0x100: "no target session",
    0x101: "target state unknown",
    0x102: "transport error",
}


class Activity:
    def __init__(self, runtime):
        self.runtime = runtime
        self.image_hash = None
        self.rules = {}
        self.groups = {}
        self.pending = {}
        self.seen = deque(maxlen=128)
        self.wake = asyncio.Event()
        self.store = Store(
            runtime.hass, 1, f"{DOMAIN}.{runtime.entry.entry_id}.activity"
        )

    def configure(self, image_hash, rules):
        if self.image_hash == image_hash and self.rules == rules:
            return
        if self.image_hash != image_hash:
            self.pending.clear()
        self.image_hash, self.rules = image_hash, rules
        members = {}
        for key, row in rules.items():
            members.setdefault((row.entity_id, row.trigger), set()).add(key)
        self.groups = {key: group for group in members.values() for key in group}
        self.wake.set()

    async def read(self):
        rt = self.runtime
        if rt.channel is None:
            return await native.read_activity(rt.client(), rt.node)
        try:
            return await rt.channel.activity(rt.monitor.client)
        except JournalUnsupported:
            raise
        except CHANNEL_ERRORS as channel_error:
            if not rt.native_status_readable:
                raise
            try:
                return await native.read_activity(rt.client(), rt.node)
            except JournalUnsupported:
                # The configured management channel may recover independently.
                raise channel_error from None

    def record(self, snapshot):
        if not isinstance(snapshot, dict) or "hash" not in snapshot:
            raise ReflexError("Invalid ESP activity snapshot")
        if snapshot["hash"] != self.image_hash:
            self.runtime.acl_synced = False
            self.runtime.dirty.set()
            return
        journal = snapshot.get("journal")
        if not isinstance(journal, str) or not re.fullmatch(
            r"(?:[0-9a-f]{28}){0,32}", journal
        ):
            raise ReflexError("Invalid ESP activity journal")

        for offset in range(0, len(journal), 28):
            run = journal[offset : offset + 16]
            rule_id = int(journal[offset + 16 : offset + 24], 16)
            group = self.groups.get(rule_id)
            if group is None:
                continue
            # Rule IDs are stable across entity renames.
            key = f"{run}/{min(group)}"
            if key in self.seen:
                continue
            outcomes = self.pending.setdefault(key, {})
            outcomes[rule_id] = int(journal[offset + 24 : offset + 28], 16)
            if len(outcomes) != len(group):
                continue
            row = self.rules[rule_id]
            details = ", ".join(
                f"{self.rules[i].target} ({RESULTS.get(status, f'Matter status {status:#x}')})"
                for i, status in sorted(outcomes.items())
            )
            hass = self.runtime.hass
            state = hass.states.get(row.entity_id)
            mode = "Direct reflex" if row.direct else "Fallback reflex"
            async_log_entry(
                hass,
                state.name if state else row.entity_id,
                f"{mode} handled by ESP: {details}",
                DOMAIN,
                row.entity_id,
            )
            del self.pending[key]
            self.seen.append(key)
            self.store.async_delay_save(lambda: list(self.seen), 2)

        # Retain incomplete groups across ring rotation, with a fixed memory bound.
        while len(self.pending) > 128:
            del self.pending[next(iter(self.pending))]

    async def run(self):
        if not self.seen:
            self.seen.extend(await self.store.async_load() or [])
        unsupported = None
        while True:
            self.wake.clear()
            retry = False
            direct = any(row.direct for row in self.rules.values())
            status = self.runtime.status
            # In standby a fallback-only ESP with a zero counter has no journal
            # to fetch. Status changes and reconnects wake this task.
            read_journal = self.rules and (
                direct or status is None or status["executions"]
            )
            identity = (
                self.runtime.monitor.generation,
                self.runtime.monitor.software_version,
            )
            if read_journal and identity != unsupported:
                try:
                    image = self.image_hash
                    generation = self.runtime.monitor.generation
                    async with asyncio.timeout(15):
                        snapshot = await self.read()
                    if (
                        image == self.image_hash
                        and generation == self.runtime.monitor.generation
                    ):
                        self.record(snapshot)
                except JournalUnsupported:
                    unsupported = identity
                except CHANNEL_ERRORS:
                    retry = True
            try:
                # Servers without vendor attribute reports still expose the journal
                # through the same authenticated management path as the rules.
                poll = retry or (
                    direct
                    and identity != unsupported
                    and not self.runtime.monitor.confirmed
                )
                async with asyncio.timeout(30 if poll else None):
                    await self.wake.wait()
            except TimeoutError:
                pass
