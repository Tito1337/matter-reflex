"""Native HA automation conditions that select the executor for this trigger."""

from typing import ClassVar

import probatio
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers.condition import Condition

from .const import DOMAIN
from .policy import ADVANCED_CONDITION, HANDLERS, MODE_CONDITION, MODES, handlers_for


class ReflexMode(Condition):
    kind = MODE_CONDITION
    fields: ClassVar[dict] = {
        probatio.Required("mode", default="fallback"): probatio.In(MODES)
    }

    @classmethod
    async def async_validate_config(cls, hass, config):
        schema = probatio.Schema(
            {
                probatio.Required("options"): {
                    probatio.Required("esp"): str,
                    **cls.fields,
                }
            }
        )
        config = schema(config)
        try:
            handlers_for(cls.kind, config["options"])
        except ValueError as exc:
            raise probatio.Invalid(str(exc)) from exc
        return config

    def __init__(self, hass, config):
        super().__init__(hass, config)
        self.entry_id = config.options["esp"]
        self.handlers = handlers_for(self.kind, config.options)

    def _async_check(self, **kwargs):
        if self.handlers.normal == "ha":
            return True
        if self.handlers.degraded != "ha":
            return False
        entry = self._hass.config_entries.async_get_entry(self.entry_id)
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state != ConfigEntryState.LOADED
        ):
            return False
        runtime = getattr(entry, "runtime_data", None)
        return bool(runtime and runtime.esp_lease_expired)


class AdvancedHandlers(ReflexMode):
    kind = ADVANCED_CONDITION
    fields: ClassVar[dict] = {
        probatio.Required(key): probatio.In(HANDLERS)
        for key in ("normal_handler", "degraded_handler")
    }


async def async_get_conditions(hass):
    return {"mode": ReflexMode, "handlers": AdvancedHandlers}
