"""Matter discovery and encrypted ESPHome management."""

import asyncio
import hashlib
import re

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from matter_server.client.exceptions import MatterClientException
from matter_server.common.errors import MatterError

from .const import CLUSTER, ENDPOINT, VENDOR


class ReflexError(Exception):
    """A protocol or safety condition prevents the operation."""


class JournalUnsupported(ReflexError):
    """The authenticated peer has no execution journal."""


CHANNEL_ERRORS = (
    ReflexError,
    MatterClientException,
    MatterError,
    aiohttp.ClientError,  # Matter Server uses aiohttp for its encrypted WebSocket client.
    OSError,
    TimeoutError,
    ValueError,
)


def client_for(hass, matter_entry_id, fabric):
    entry = hass.config_entries.async_get_entry(matter_entry_id)
    if entry is None or entry.state != ConfigEntryState.LOADED:
        raise ReflexError("Matter integration is offline")
    client = entry.runtime_data.adapter.matter_client
    if not client.server_info or client.server_info.compressed_fabric_id != fabric:
        raise ReflexError("Connected Matter fabric differs from the configured fabric")
    return client


def is_reflex(node):
    attrs = node.node_data.attributes
    return (
        attrs.get("0/40/2") == VENDOR
        and attrs.get("0/40/3") == "Matter Reflex"
        and CLUSTER in (attrs.get(f"{ENDPOINT}/29/1") or [])
        and 6 in (attrs.get("2/29/1") or [])
    )


def checked_status(data, node_id):
    if (
        not isinstance(data, dict)
        or data.get("node_id") != f"{node_id:016x}"
        or (
            "ready" in data
            and (type(data["ready"]) is not int or not 0 <= data["ready"] <= 0xFFFFFFFF)
        )
        or type(data.get("active")) is not bool
        or type(data.get("executions")) is not int
        or not 0 <= data["executions"] <= 0xFFFFFFFF
        or not isinstance(data.get("hash"), str)
        or not re.fullmatch("(?:[0-9a-f]{64})?", data["hash"])
    ):
        raise ReflexError("Unexpected status or Matter node identity")
    return data


def esphome_action(hass, entry):
    """Resolve an action on a loaded, encrypted ESPHome entry."""
    if (
        entry is None
        or entry.domain != "esphome"
        or entry.state != ConfigEntryState.LOADED
    ):
        return None
    if not entry.data.get("noise_psk") or not entry.runtime_data.available:
        return None
    info = entry.runtime_data.device_info
    if info is None:
        return None
    action = f"{info.name.replace('-', '_')}_reflex_exchange"
    return action if hass.services.has_service("esphome", action) else None


class ESPHomeChannel:
    """Use the existing encrypted ESPHome connection and verify the Matter owner."""

    def __init__(self, hass, node_id, fabric, entry_id):
        self.hass = hass
        self.node_id = node_id
        self.fabric = f"{fabric:016x}"
        self.entry_id = entry_id

    async def exchange(self, image=b""):
        entry = self.hass.config_entries.async_get_entry(self.entry_id)
        action = esphome_action(self.hass, entry)
        if not action:
            raise ReflexError("Encrypted ESPHome action is unavailable")
        try:
            async with asyncio.timeout(10):
                result = await self.hass.services.async_call(
                    "esphome",
                    action,
                    {
                        "fabric": self.fabric,
                        "node": f"{self.node_id:016x}",
                        "image": image.hex(),
                    },
                    blocking=True,
                    return_response=True,
                )
        except HomeAssistantError as err:
            raise ReflexError("ESPHome rules action failed") from err
        if not isinstance(result, dict) or result.get("fabric") != self.fabric:
            raise ReflexError(
                "ESPHome action rejected the request or the Matter fabric differs"
            )
        status = checked_status(result, self.node_id)
        if image and status["hash"] != hashlib.sha256(image).hexdigest():
            raise ReflexError("ESPHome board did not commit the expected rule hash")
        return status

    async def activity(self, client):
        status = await self.exchange()
        if "journal" not in status:
            raise JournalUnsupported("ESPHome board has no activity journal")
        return status

    async def status(self, client):
        return await self.exchange()

    async def send_rules(self, client, image):
        await self.exchange(image)
