"""Select a commissioned Reflex board and negotiate its rules transport."""

import asyncio

import voluptuous as vol
from homeassistant import config_entries
from matter_server.common.errors import InvalidArguments, InvalidCommand

from . import native
from .const import DOMAIN
from .transport import (
    CHANNEL_ERRORS,
    ESPHomeChannel,
    ReflexError,
    esphome_action,
    is_reflex,
)


class ReflexConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def _entry(self, mode, esphome_entry_id=None):
        data = {**self._selection, "mode": mode}
        if esphome_entry_id is not None:
            data["esphome_entry_id"] = esphome_entry_id
        if self.context.get("source") == config_entries.SOURCE_RECONFIGURE:
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(), data=data
            )
        return self.async_create_entry(
            title=f"Matter Reflex ({data['node_id']})", data=data
        )

    async def async_step_user(self, user_input=None):
        candidates = {}
        for entry in self.hass.config_entries.async_loaded_entries("matter"):
            client = entry.runtime_data.adapter.matter_client
            if client.server_info is None:
                continue
            for node in client.get_nodes():
                if is_reflex(node):
                    candidates[f"{entry.entry_id}/{node.node_id}"] = (
                        entry,
                        client,
                        node,
                    )
        if not candidates:
            return self.async_abort(reason="no_nodes")

        errors = {}
        if user_input is not None:
            selected = candidates.get(user_input["node"])
            if selected:
                entry, client, node = selected
                fabric = client.server_info.compressed_fabric_id
                self._selection = {
                    "matter_entry_id": entry.entry_id,
                    "node_id": node.node_id,
                    "fabric": fabric,
                }
                await self.async_set_unique_id(f"{fabric:016x}-{node.node_id:016x}")
                self._abort_if_unique_id_configured()
                try:
                    async with asyncio.timeout(10):
                        use_action = await native.supports_lease_action(
                            client, node.node_id
                        )
                        await native.renew(client, node.node_id, use_action)
                        try:
                            await native.probe(client, node.node_id)
                        except (ReflexError, InvalidArguments, InvalidCommand):
                            return await self.async_step_esphome()
                except native.LeaseUnsupported:
                    errors["base"] = "lease_unsupported"
                except CHANNEL_ERRORS:
                    errors["base"] = "matter_unavailable"
                else:
                    return self._entry("matter")
            else:
                errors["base"] = "invalid_input"
        choices = {
            k: f"{entry.title} · Matter Reflex ({node.node_id})"
            for k, (entry, _, node) in candidates.items()
        }
        return self.async_show_form(
            step_id="user",
            errors=errors,
            data_schema=vol.Schema({vol.Required("node"): vol.In(choices)}),
        )

    async def async_step_reconfigure(self, user_input=None):
        entry = self._get_reconfigure_entry()
        self._selection = {
            k: entry.data[k] for k in ("node_id", "fabric", "matter_entry_id")
        }
        return await self.async_step_esphome(user_input)

    async def async_step_esphome(self, user_input=None):
        errors = {}
        if user_input is not None:
            selected = self._selection
            api_id = user_input.get("esphome_entry_id")
            try:
                if api_id:
                    channel = ESPHomeChannel(
                        self.hass, selected["node_id"], selected["fabric"], api_id
                    )
                    await channel.status(None)
                    return self._entry("esphome", esphome_entry_id=api_id)
                errors["base"] = "invalid_input"
            except CHANNEL_ERRORS:
                errors["base"] = "cannot_connect"
        api_choices = {
            entry.entry_id: entry.title
            for entry in self.hass.config_entries.async_loaded_entries("esphome")
            if esphome_action(self.hass, entry)
        }
        if not api_choices:
            return self.async_abort(reason="no_esphome")
        return self.async_show_form(
            step_id="esphome",
            errors=errors,
            data_schema=vol.Schema(
                {vol.Required("esphome_entry_id"): vol.In(api_choices)}
            ),
        )
