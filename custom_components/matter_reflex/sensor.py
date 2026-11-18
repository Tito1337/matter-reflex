"""Reflex execution count and last successful rule synchronization."""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)

from .entity import ReflexEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [
            ExecutionCount(entry.runtime_data, "executions"),
            LastSync(entry.runtime_data, "last_sync"),
        ]
    )


class ExecutionCount(ReflexEntity, SensorEntity):
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def available(self):
        return self.runtime.status is not None

    @property
    def native_value(self):
        return self.runtime.status["executions"] if self.runtime.status else None


class LastSync(ReflexEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        return self.runtime.last_sync
