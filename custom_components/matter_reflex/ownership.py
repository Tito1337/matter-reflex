"""Reciprocal lease shared by automation conditions and config-entry runtimes."""

from dataclasses import dataclass
from time import monotonic

from homeassistant.core import CoreState, callback
from homeassistant.helpers.start import async_at_started

from .const import DOMAIN, LEASE_SECONDS


@dataclass
class EspLease:
    deadline: float | None = None

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
            if lease.deadline is None:
                lease.renew()

        if hass.state is CoreState.running:
            start_grace(hass)
        else:
            async_at_started(hass, start_grace)
    return leases[entry_id]
