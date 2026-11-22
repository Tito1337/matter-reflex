"""Reciprocal lease shared by automation conditions and config-entry runtimes."""

from dataclasses import asdict, dataclass
from time import monotonic

from homeassistant.core import CoreState, callback
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store

from .compiler import ActivityRule
from .const import DOMAIN, LEASE_SECONDS
from .wire import Rule, digest, encode


@dataclass
class EspLease:
    deadline: float | None = None
    startup_deadline: float | None = None

    def start(self):
        self.startup_deadline = monotonic() + LEASE_SECONDS
        if self.deadline is None:
            self.renew()

    def starting(self):
        return self.startup_deadline is None or monotonic() < self.startup_deadline

    def renew(self):
        self.deadline = monotonic() + LEASE_SECONDS

    def expired(self, hass):
        return (
            hass.state is CoreState.running
            and self.deadline is not None
            and monotonic() >= self.deadline
        )


def lease_for(hass, entry_id):
    leases = hass.data.setdefault(f"{DOMAIN}.esp_leases", {})
    if entry_id not in leases:
        lease = leases[entry_id] = EspLease()

        @callback
        def start_grace(_hass):
            lease.start()

        if hass.state is CoreState.running:
            start_grace(hass)
        else:
            async_at_started(hass, start_grace)
    return leases[entry_id]


class OwnershipImages:
    """Keep the last observed and next candidate maps across a lost commit reply."""

    def __init__(self, hass, entry):
        self.store = Store(
            hass, 1, f"{DOMAIN}.{entry.entry_id}.images", atomic_writes=True
        )
        self.readiness_store = Store(
            hass, 1, f"{DOMAIN}.{entry.entry_id}.readiness", atomic_writes=True
        )
        self.last_ready = None
        self.identity = [entry.data["fabric"], entry.data["node_id"]]
        self.maps = {}

    def remember_ready(self, image, mask):
        proof = {"identity": self.identity, "hash": image, "ready": mask}
        if proof != self.last_ready:
            self.last_ready = proof
            self.readiness_store.async_delay_save(lambda: self.last_ready, 1)

    async def load(self):
        proof = await self.readiness_store.async_load()
        if proof and proof.get("identity") == self.identity:
            self.last_ready = proof
        saved = await self.store.async_load()
        if not saved or saved.get("identity") != self.identity:
            return
        for key, rows in saved["images"].items():
            decoded = [
                ActivityRule(**{**item, "row": Rule(**item["row"])}) for item in rows
            ]
            if digest(encode([item.row for item in decoded])) == key:
                self.maps[key] = {item.row.rule_id: item for item in decoded}

    async def prepare(self, expected, rules, installed):
        keep = {k: v for k, v in self.maps.items() if k == installed}
        keep[expected] = rules
        if self.maps == keep:
            return
        await self.store.async_save(
            {
                "identity": self.identity,
                "images": {
                    k: [asdict(row) for row in group.values()]
                    for k, group in keep.items()
                },
            }
        )
        self.maps = keep
