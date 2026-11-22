"""Report completed ESP runs in HA Activity, grouped across target commands."""

import asyncio
import re
from collections import deque

from homeassistant.components.logbook.helpers import async_log_entry
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .native import PREFIX
from .transport import CHANNEL_ERRORS, ReflexError

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
        self.seen = deque(maxlen=128)
        self.wake = asyncio.Event()
        self.store = Store(
            runtime.hass, 1, f"{DOMAIN}.{runtime.entry.entry_id}.activity"
        )

    def configure(self, image_hash, rules):
        if self.image_hash != image_hash or self.rules != rules:
            self.image_hash, self.rules = image_hash, rules
            self.wake.set()

    async def read(self):
        rt = self.runtime
        if rt.channel is not None:
            return await rt.channel.activity(rt.monitor.client)
        paths = [PREFIX + str(i) for i in range(5, 9)]
        data = await rt.client().read_attribute(rt.node, [PREFIX + "2", *paths])
        if not isinstance(data, dict) or not all(
            isinstance(data.get(p), str) for p in paths
        ):
            raise ReflexError("ESP activity journal is unavailable")
        return {
            "hash": data.get(PREFIX + "2"),
            "journal": "".join(data[p] for p in paths),
        }

    def record(self, snapshot):
        if not isinstance(snapshot, dict) or snapshot.get("hash") != self.image_hash:
            return
        journal = snapshot.get("journal")
        if not isinstance(journal, str) or not re.fullmatch(
            r"(?:[0-9a-f]{28}){0,32}", journal
        ):
            raise ReflexError("Invalid ESP activity journal")

        groups = {}
        for offset in range(0, len(journal), 28):
            run = journal[offset : offset + 16]
            rule_id = int(journal[offset + 16 : offset + 24], 16)
            rule = self.rules.get(rule_id)
            if rule is not None:
                key = (run, rule.entity_id, rule.trigger)
                groups.setdefault(key, {})[rule_id] = int(
                    journal[offset + 24 : offset + 28], 16
                )

        for (run, entity, trigger), outcomes in groups.items():
            # The rule ID incorporates the automation unique ID, independently of renames.
            key = f"{run}/{min(outcomes)}"
            expected = {
                i
                for i, row in self.rules.items()
                if (row.entity_id, row.trigger) == (entity, trigger)
            }
            if outcomes.keys() != expected or key in self.seen:
                continue
            details = ", ".join(
                f"{self.rules[i].target} ({RESULTS.get(status, f'Matter status {status:#x}')})"
                for i, status in sorted(outcomes.items())
            )
            hass = self.runtime.hass
            state = hass.states.get(entity)
            async_log_entry(
                hass,
                state.name if state else entity,
                f"Direct reflex handled by ESP: {details}",
                DOMAIN,
                entity,
            )
            self.seen.append(key)
            self.store.async_delay_save(lambda: list(self.seen), 2)

    async def run(self):
        self.seen.extend(await self.store.async_load() or [])
        while True:
            self.wake.clear()
            retry = False
            if self.rules:
                try:
                    async with asyncio.timeout(12):
                        self.record(await self.read())
                except CHANNEL_ERRORS:
                    retry = True
            try:
                # Servers without vendor attribute reports still expose the journal
                # through the same authenticated management path as the rules.
                poll = self.rules and (retry or not self.runtime.monitor.confirmed)
                async with asyncio.timeout(30 if poll else None):
                    await self.wake.wait()
            except TimeoutError:
                pass
