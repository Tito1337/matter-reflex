"""Reflex mode and committed ruleset state."""

from homeassistant.components.binary_sensor import BinarySensorEntity

from .entity import ReflexEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        ReflexBinarySensor(entry.runtime_data, k) for k in ("active", "synced")
    )


class ReflexBinarySensor(ReflexEntity, BinarySensorEntity):
    @property
    def available(self):
        return self.runtime.status is not None

    @property
    def is_on(self):
        runtime = self.runtime
        if not runtime.status:
            return None
        if self.key == "active":
            return runtime.status["active"]
        return runtime.acl_synced and runtime.status["hash"] == runtime.target_hash
