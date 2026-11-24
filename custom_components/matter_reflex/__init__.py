"""Matter Reflex Home Assistant integration."""

from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .registry import async_setup_entity_cleanup
from .runtime import Runtime
from .transport import ReflexError

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass, entry):
    runtime = Runtime(hass, entry)
    try:
        client = runtime.client()
    except ReflexError as err:
        raise ConfigEntryNotReady(str(err)) from err
    entry.runtime_data = runtime
    async_setup_entity_cleanup(hass, entry, client.server_info)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await runtime.start()
    return True


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
