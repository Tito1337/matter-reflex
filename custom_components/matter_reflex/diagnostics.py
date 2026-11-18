"""Provide local, allowlisted diagnostics for Matter Reflex."""

from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant

from .runtime import Runtime


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry[Runtime]
) -> dict[str, Any]:
    """Report only technical state already held by the integration."""
    runtime = (
        getattr(entry, "runtime_data", None)
        if entry.state is ConfigEntryState.LOADED
        else None
    )
    status = runtime.status if runtime is not None else None
    transport = None
    if runtime is not None:
        transport = "http" if runtime.channel is not None else "matter"

    return {
        "rules_transport": transport,
        "status_available": status is not None,
        "reflex_active": status["active"] if status is not None else None,
        "rules_synchronized": (
            runtime.rules_synchronized if runtime is not None else None
        ),
        "reported_rule_results_since_boot": (
            status["executions"] if status is not None else None
        ),
    }
