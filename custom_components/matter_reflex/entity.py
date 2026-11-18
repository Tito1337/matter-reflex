"""Shared coordinator and Matter registry association for Reflex diagnostics."""

from homeassistant.components.matter.helpers import get_node_device_identifier
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity, EntityCategory


class ReflexEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime, key):
        self.runtime = runtime
        self.key = self._attr_translation_key = key
        self._attr_unique_id = f"{runtime.entry.unique_id}-{key}"
        server = runtime.client().server_info
        self._attr_device_info = DeviceInfo(
            identifiers={get_node_device_identifier(server, runtime.node)}
        )

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.runtime.signal, self.async_write_ha_state
            )
        )
