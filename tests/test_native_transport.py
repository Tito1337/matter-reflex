"""Exercise negotiated rules transport and repeated standard Matter leases."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import CoreState

from custom_components.matter_reflex import config_flow, native
from custom_components.matter_reflex.const import CLUSTER, VENDOR
from custom_components.matter_reflex.runtime import Runtime
from custom_components.matter_reflex.transport import ReflexError


def board(node_id=31):
    return SimpleNamespace(
        node_id=node_id,
        node_data=SimpleNamespace(
            attributes={
                "0/40/2": VENDOR,
                "0/40/3": "Matter Reflex",
                "1/29/1": [CLUSTER],
                "2/29/1": [6],
            }
        ),
    )


@pytest.mark.asyncio
async def test_heartbeat_is_a_standard_matter_invoke_every_interval(monkeypatch):
    runtime = Runtime.__new__(Runtime)
    runtime.hass = SimpleNamespace(state=CoreState.running)
    runtime.node = 31
    runtime.channel = Mock()
    client = SimpleNamespace(
        send_device_command=AsyncMock(), get_node=Mock(return_value=board())
    )
    runtime.client = Mock(return_value=client)
    monkeypatch.setattr(
        asyncio, "sleep", AsyncMock(side_effect=[None, asyncio.CancelledError()])
    )
    with pytest.raises(asyncio.CancelledError):
        await runtime._lease_loop()
    assert client.send_device_command.await_count == 2
    for call in client.send_device_command.await_args_list:
        node, ep, command = call.args
        assert (node, ep, command.cluster_id, command.command_id) == (31, 2, 6, 0x42)
        assert (command.onOffControl, command.onTime, command.offWaitTime) == (
            0,
            100,
            0,
        )
    runtime.channel.request.assert_not_called()


@pytest.mark.asyncio
async def test_native_transfer_is_chunked_and_verified_per_frame():
    runtime = Runtime.__new__(Runtime)
    runtime.node, runtime.channel = 31, None
    client = SimpleNamespace(write_attribute=AsyncMock(return_value=[{"Status": 0}]))
    image = bytes(range(220))
    await runtime.transfer(client, image)
    frames = [c.args[2] for c in client.write_attribute.await_args_list]
    assert frames[0] == f"B{native.digest(image)}00dc"
    assert frames[-1] == f"C{native.digest(image)}"
    assert all(
        c.args[:2] == (31, native.PREFIX + "1")
        for c in client.write_attribute.await_args_list
    )
    assert [int(frame[1:5], 16) for frame in frames[1:-1]] == [0, 96, 192]
    assert b"".join(bytes.fromhex(frame[5:]) for frame in frames[1:-1]) == image
    client.write_attribute = AsyncMock(side_effect=[[{"Status": 0}], [{"Status": 135}]])
    with pytest.raises(ReflexError):
        await runtime.transfer(client, image)
    assert client.write_attribute.await_count == 2


@pytest.mark.asyncio
async def test_http_mode_still_prefers_matter_status_then_falls_back():
    runtime = Runtime.__new__(Runtime)
    runtime.node, runtime.channel = (
        31,
        SimpleNamespace(status=AsyncMock(return_value="fallback")),
    )
    client = SimpleNamespace(
        read_attribute=AsyncMock(
            return_value={
                native.PREFIX + "2": "",
                native.PREFIX + "3": False,
                native.PREFIX + "4": 1,
            }
        )
    )
    assert (await runtime.read_status(client))["executions"] == 1
    runtime.channel.status.assert_not_awaited()
    client.read_attribute.return_value = {}
    assert await runtime.read_status(client) == "fallback"
    runtime.channel = None
    with pytest.raises(ReflexError):
        await runtime.read_status(client)


def flow_with_node():
    flow = config_flow.ReflexConfigFlow()
    client = SimpleNamespace(
        get_nodes=Mock(return_value=[board()]),
        get_node=Mock(return_value=board()),
        server_info=SimpleNamespace(compressed_fabric_id=42),
    )
    entry = SimpleNamespace(
        entry_id="fabric",
        title="Matter",
        runtime_data=SimpleNamespace(adapter=SimpleNamespace(matter_client=client)),
    )
    flow.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_loaded_entries=Mock(return_value=[entry]))
    )
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = Mock()
    flow.async_create_entry = Mock(side_effect=lambda **kwargs: kwargs)
    flow.async_show_form = Mock(side_effect=lambda **kwargs: kwargs)
    return flow, client


@pytest.mark.asyncio
async def test_probe_chooses_native_without_a_key(monkeypatch):
    flow, client = flow_with_node()
    probe = AsyncMock()
    monkeypatch.setattr(native, "probe", probe)
    result = await flow.async_step_user({"node": "fabric/31"})
    probe.assert_awaited_once_with(client, 31)
    assert result["data"] == {
        "node_id": 31,
        "fabric": 42,
        "mode": "matter",
        "matter_entry_id": "fabric",
    }


@pytest.mark.asyncio
async def test_only_a_rejected_vendor_probe_asks_for_authenticated_fallback(
    monkeypatch,
):
    flow, client = flow_with_node()
    monkeypatch.setattr(native, "probe", AsyncMock(side_effect=TimeoutError))
    assert (await flow.async_step_user({"node": "fabric/31"}))["errors"] == {
        "base": "matter_unavailable"
    }
    monkeypatch.setattr(
        native, "probe", AsyncMock(side_effect=ReflexError("unknown cluster"))
    )
    assert (await flow.async_step_user({"node": "fabric/31"}))["step_id"] == "fallback"
    key = "12" * 32
    validated = AsyncMock()
    monkeypatch.setattr(config_flow, "client_for", Mock(return_value=client))
    monkeypatch.setattr(
        config_flow, "Channel", Mock(return_value=SimpleNamespace(status=validated))
    )
    result = await flow.async_step_fallback({"key": key})
    validated.assert_awaited_once_with(client)
    assert result["data"]["mode"] == "http" and result["data"]["key"] == key
