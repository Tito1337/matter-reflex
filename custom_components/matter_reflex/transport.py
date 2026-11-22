"""Matter node discovery and authenticated Thread control channel."""

import asyncio
import hashlib
import hmac
import json
import re
from ipaddress import IPv6Address

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from matter_server.client.exceptions import MatterClientException
from matter_server.common.errors import MatterError

from .const import CLUSTER, ENDPOINT, PORT, VENDOR


class ReflexError(Exception):
    """A protocol or safety condition prevents the operation."""


CHANNEL_ERRORS = (
    ReflexError,
    MatterClientException,
    MatterError,
    aiohttp.ClientError,
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


def signature(key, *parts, body):
    message = "\n".join((*parts, hashlib.sha256(body).hexdigest()))
    return hmac.new(key, message.encode("ascii"), hashlib.sha256).hexdigest()


class Channel:
    """Authenticate both directions using a fresh challenge on each exchange."""

    def __init__(self, hass, node_id, key):
        self.session = async_get_clientsession(hass)
        self.node_id = node_id
        self.key = bytes.fromhex(key)
        self.address = None

    async def request(self, client, action, body=b""):
        path = f"/v1/{action}"
        # Try the last authenticated route first. Mesh-local addresses may not
        # be routed from HA; try the remaining OMR/GUA addresses on failure.
        addresses = [self.address] if self.address else []
        tried = set()
        for attempt in range(2):
            if attempt == 1:
                if client is None:
                    break
                async with asyncio.timeout(4):
                    addresses = await client.get_node_ip_addresses(
                        self.node_id, prefer_cache=False
                    )
            for address in addresses:
                try:
                    ip = IPv6Address(address)
                except ValueError:
                    continue
                if (
                    ip.is_link_local
                    or ip.is_loopback
                    or ip.is_multicast
                    or ip.is_unspecified
                    or ip.ipv4_mapped
                    or ip.scope_id
                    or ip in tried
                ):
                    continue
                tried.add(ip)
                try:
                    async with asyncio.timeout(2):
                        result = await self._exchange(
                            f"http://[{ip}]:{PORT}", path, body
                        )
                except (aiohttp.ClientError, OSError, TimeoutError, ReflexError):
                    continue
                self.address = str(ip)
                return result
        self.address = None
        raise ReflexError("No reachable authenticated Thread address")

    async def _read(self, response, maximum):
        if response.status != 200:
            raise ReflexError(f"Reflex HTTP status {response.status}")
        # read(n) may return a partial TCP packet. Bound memory and wait through EOF.
        data = bytearray()
        async for chunk in response.content.iter_chunked(512):
            data.extend(chunk)
            if len(data) > maximum:
                raise ReflexError("Oversized Reflex response")
        return bytes(data)

    async def _exchange(self, base, path, body):
        async with self.session.get(
            base + "/v1/challenge", allow_redirects=False
        ) as resp:
            nonce_bytes = await self._read(resp, 32)
        if not re.fullmatch(b"[0-9a-f]{32}", nonce_bytes):
            raise ReflexError("Invalid challenge")
        nonce = nonce_bytes.decode("ascii")
        headers = {
            "X-Reflex-Nonce": nonce,
            "X-Reflex-Mac": signature(self.key, "REQ", nonce, path, body=body),
            "Content-Type": "application/octet-stream",
        }
        async with self.session.post(
            base + path, data=body, headers=headers, allow_redirects=False
        ) as resp:
            reply = await self._read(resp, 1200 if path == "/v1/activity" else 512)
            mac = resp.headers.get("X-Reflex-Mac", "")
        expected = signature(self.key, "RES", nonce, path, "200", body=reply)
        if not re.fullmatch("[0-9a-f]{64}", mac) or not hmac.compare_digest(
            mac, expected
        ):
            raise ReflexError("Unauthenticated Reflex response")
        return reply

    async def send_rules(self, client, image):
        await self.request(client, "rules", image)

    async def activity(self, client):
        return json.loads(await self.request(client, "activity"))

    async def status(self, client):
        data = json.loads(await self.request(client, "status"))
        return checked_status(data, self.node_id)


def checked_status(data, node_id):
    if (
        not isinstance(data, dict)
        or data.get("node_id") != f"{node_id:016x}"
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
        return await self.exchange()

    async def status(self, client):
        return await self.exchange()

    async def send_rules(self, client, image):
        await self.exchange(image)
