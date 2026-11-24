"""Keep internal Matter compatibility controls out of the device UI."""

from homeassistant.components.matter.helpers import get_node_device_identifier
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

# These entities belong to Reflex's reserved endpoints, not user-added outputs.
# Keep the protocol endpoints intact for older controllers and commissioning.
INTERNAL_ENTITIES = {
    ("button", "1-IdentifyButton-3-1"),
    ("button", "2-IdentifyButton-3-1"),
    ("switch", "2-MatterPlug-6-0"),
}


@callback
def async_setup_entity_cleanup(hass, entry, server_info):
    """Disable unused controls already registered or discovered after setup."""
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    identifier = get_node_device_identifier(server_info, entry.data["node_id"])

    @callback
    def disable_internal(entity):
        if (
            entity is None
            or entity.platform != "matter"
            or entity.config_entry_id != entry.data["matter_entry_id"]
            or entity.disabled_by is not None
        ):
            return
        device = devices.async_get_device(identifiers={identifier})
        if device is None or entity.device_id != device.id:
            return
        suffix = "-".join(entity.unique_id.rsplit("-", 4)[1:])
        if (entity.domain, suffix) in INTERNAL_ENTITIES:
            registry.async_update_entity(
                entity.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION
            )

    @callback
    def entity_created(event):
        if event.data["action"] == "create":
            disable_internal(registry.async_get(event.data["entity_id"]))

    entry.async_on_unload(
        hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, entity_created)
    )
    for entity in er.async_entries_for_config_entry(
        registry, entry.data["matter_entry_id"]
    ):
        disable_internal(entity)
