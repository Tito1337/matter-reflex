"""Select a commissioned Reflex board and negotiate its rules transport."""

import asyncio
import re

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector
from matter_server.common.errors import InvalidArguments, InvalidCommand

from . import native
from .const import DOMAIN
from .transport import CHANNEL_ERRORS, Channel, ReflexError, client_for, is_reflex


class ReflexConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def _entry(self, mode, key=None):
        data = {**self._selection, "mode": mode}
        if key is not None:
            data["key"] = key
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
                            return await self.async_step_fallback()
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

    async def async_step_fallback(self, user_input=None):
        errors = {}
        if user_input is not None:
            key = user_input["key"].strip().lower()
            if re.fullmatch("[0-9a-f]{64}", key):
                try:
                    selected = self._selection
                    client = client_for(
                        self.hass, selected["matter_entry_id"], selected["fabric"]
                    )
                    if not is_reflex(client.get_node(selected["node_id"])):
                        raise ReflexError("Node changed during configuration")
                    async with asyncio.timeout(10):
                        await Channel(self.hass, selected["node_id"], key).status(
                            client
                        )
                except CHANNEL_ERRORS:
                    errors["base"] = "cannot_connect"
                else:
                    return self._entry("http", key)
            else:
                errors["base"] = "invalid_input"
        return self.async_show_form(
            step_id="fallback",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required("key"): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        )
                    )
                }
            ),
        )
