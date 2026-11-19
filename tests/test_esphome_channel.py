"""Encrypted ESPHome management uses Matter identity and commit verification."""

from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError

from custom_components.matter_reflex.transport import ESPHomeChannel, ReflexError


def channel():
    entry = SimpleNamespace(
        domain="esphome",
        state=ConfigEntryState.LOADED,
        data={"noise_psk": "configured"},
        runtime_data=SimpleNamespace(
            available=True, device_info=SimpleNamespace(name="bureau-reflex")
        ),
    )
    services = SimpleNamespace(
        has_service=Mock(return_value=True), async_call=AsyncMock()
    )
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_get_entry=lambda _: entry),
        services=services,
    )
    api = ESPHomeChannel(hass, 28, 42, "esphome-entry")
    reply = {
        "fabric": "000000000000002a",
        "node_id": "000000000000001c",
        "active": False,
        "hash": sha256(b"rules").hexdigest(),
        "executions": 0,
    }
    services.async_call.return_value = reply
    return api, entry, services, reply


@pytest.mark.asyncio
async def test_api_status_and_atomic_transfer():
    api, _, services, reply = channel()
    assert await api.status(None) == reply
    await api.send_rules(None, b"rules")
    assert [c.args[2]["image"] for c in services.async_call.await_args_list] == [
        "",
        b"rules".hex(),
    ]
    services.async_call.assert_awaited_with(
        "esphome",
        "bureau_reflex_reflex_exchange",
        {"fabric": reply["fabric"], "node": reply["node_id"], "image": b"rules".hex()},
        blocking=True,
        return_response=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [("node_id", "wrong"), ("fabric", "wrong"), ("hash", ""), ("executions", True)],
)
async def test_api_rejects_mismatched_reply(field, value):
    api, _, _, reply = channel()
    reply[field] = value
    with pytest.raises(ReflexError):
        await api.send_rules(None, b"rules")


@pytest.mark.asyncio
async def test_api_requires_encryption_and_handles_disconnect():
    api, entry, services, _ = channel()
    services.async_call.side_effect = HomeAssistantError("offline")
    with pytest.raises(ReflexError):
        await api.status(None)
    entry.data.clear()
    with pytest.raises(ReflexError):
        await api.send_rules(None, b"rules")
    services.async_call.assert_awaited_once()
