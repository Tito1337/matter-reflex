"""Native HA automation conditions that select the executor for this trigger."""

from typing import ClassVar

import probatio
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from homeassistant.helpers.condition import Condition

from .const import DOMAIN, REFLEX_LABEL
from .ownership import lease_for
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
        self.lease = lease_for(hass, self.entry_id)

    def _async_check(self, *, variables=None, **kwargs):
        if self.handlers.normal == "ha":
            return True
        # HA passes the triggering automation's state dictionary as "this".
        # Label membership can change without reloading the automation.
        this = (variables or {}).get("this")
        entity_id = this.get("entity_id") if isinstance(this, dict) else None
        if not isinstance(entity_id, str) or not entity_id.startswith("automation."):
            return True
        reg = er.async_get(self._hass).async_get(entity_id)
        label = lr.async_get(self._hass).async_get_label_by_name(REFLEX_LABEL)
        if not reg or label is None or label.label_id not in reg.labels:
            return True
        if self.handlers.degraded != "ha":
            return False
        if self.lease.expired(self._hass):
            return True
        if self.handlers.normal != "esp":
            return False
        entry = self._hass.config_entries.async_get_entry(self.entry_id)
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state != ConfigEntryState.LOADED
        ):
            return False
        runtime = getattr(entry, "runtime_data", None)
        trigger = (variables or {}).get("trigger") or {}
        owner = runtime.esp_handles(entity_id, trigger) if runtime else None
        if owner is None:
            return not self.lease.starting()
        return not owner


class AdvancedHandlers(ReflexMode):
    kind = ADVANCED_CONDITION
    fields: ClassVar[dict] = {
        probatio.Required(key): probatio.In(HANDLERS)
        for key in ("normal_handler", "degraded_handler")
    }


async def async_get_conditions(hass):
    return {"mode": ReflexMode, "handlers": AdvancedHandlers}
