"""Exercise negotiated rules transport and repeated standard Matter leases."""

import asyncio
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import CoreState
from matter_server.common.errors import InvalidArguments, SDKStackError

from custom_components.matter_reflex import (
    config_flow,
    native,
)
from custom_components.matter_reflex.const import CLUSTER, VENDOR
from custom_components.matter_reflex.runtime import Runtime
from custom_components.matter_reflex.transport import ReflexError


def board(node_id=31, version=5):
    return SimpleNamespace(
        node_id=node_id,
        node_data=SimpleNamespace(
            attributes={
                "0/40/2": VENDOR,
                "0/40/3": "Matter Reflex",
                "0/40/9": version,
                "1/29/1": [CLUSTER, 37] if version >= 5 else [CLUSTER],
                "2/29/1": [6],
            }
        ),
    )


def identity(version=5):
    return {
        "0/40/2": VENDOR,
        "0/40/3": "Matter Reflex",
        native.SOFTWARE_VERSION_PATH: version,
    }


@pytest.fixture
def lease_clock(monkeypatch):
    clock = SimpleNamespace(now=0.0, delays=[], stop_after=3)

    async def advance(delay):
        clock.now += delay
        clock.delays.append(delay)
        if len(clock.delays) == clock.stop_after:
            raise asyncio.CancelledError

    monkeypatch.setattr(
        "custom_components.matter_reflex.runtime.monotonic", lambda: clock.now
    )
    monkeypatch.setattr(asyncio, "sleep", advance)
    return clock


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("miss_duration", "expected"),
    [(3.0, [1, 5, 9, 13]), (4.01, [1, 5, 9.01, 13]), (13.0, [1, 5, 18, 22])],
)
@pytest.mark.parametrize("failure", [TimeoutError, asyncio.CancelledError])
async def test_lease_dispatch_slots_survive_lost_renewal(
    lease_clock, miss_duration, expected, failure
):
    runtime = Runtime.__new__(Runtime)
    runtime.hass = SimpleNamespace(state=CoreState.running)
    runtime.node = 31
    runtime.clear_issue = Mock()
    starts = []
    deadlines = []

    async def discover(*args):
        if not starts:
            lease_clock.now += 1
        return identity()

    async def send(*args, **kwargs):
        starts.append(lease_clock.now)
        deadlines.append(runtime.esp_deadline)
        if len(starts) == 2:
            lease_clock.now += miss_duration
            raise failure
        lease_clock.now += 0.25

    client = SimpleNamespace(
        get_node=Mock(return_value=board()),
        read_attribute=AsyncMock(side_effect=discover),
        send_device_command=AsyncMock(side_effect=send),
    )
    runtime.client = Mock(return_value=client)
    lease_clock.stop_after = 4
    with pytest.raises(asyncio.CancelledError):
        await runtime._lease_loop()
    assert starts == pytest.approx(expected)
    # A failed or cancelled renewal cannot extend the reciprocal HA deadline.
    assert deadlines[1:3] == pytest.approx([11.25, 11.25])
    assert runtime.esp_deadline == pytest.approx(starts[-1] + 10.25)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, {"active": True}, {"active": False}])
async def test_heartbeat_is_one_named_action_every_interval(monkeypatch, status):
    runtime = Runtime.__new__(Runtime)
    runtime.hass = SimpleNamespace(state=CoreState.running)
    runtime.node = 31
    runtime.status = status
    runtime.channel = Mock()
    client = SimpleNamespace(
        read_attribute=AsyncMock(return_value=identity()),
        send_device_command=AsyncMock(),
        write_attribute=AsyncMock(),
        get_node=Mock(return_value=board()),
    )
    runtime.client = Mock(return_value=client)
    runtime.clear_issue = Mock()
    monkeypatch.setattr(
        asyncio, "sleep", AsyncMock(side_effect=[None, asyncio.CancelledError()])
    )
    with pytest.raises(asyncio.CancelledError):
        await runtime._lease_loop()
    assert client.send_device_command.await_count == 2
    for call in client.send_device_command.await_args_list:
        node, ep, command = call.args
        assert (node, ep, command.cluster_id, command.command_id) == (31, 1, 37, 0)
        assert asdict(command) == {"actionID": 1, "invokeID": None}
        assert command.ToTLV() == bytes.fromhex("15 24 00 01 18")
        assert call.kwargs == {"interaction_timeout_ms": 3000}
    client.read_attribute.assert_awaited_once_with(31, native.LEASE_IDENTITY_PATHS)
    client.write_attribute.assert_not_awaited()
    runtime.channel.request.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [0, 3, 4])
async def test_older_board_uses_one_quiet_attribute_write(version):
    client = SimpleNamespace(
        read_attribute=AsyncMock(return_value=identity(version)),
        write_attribute=AsyncMock(return_value=[{"Status": 0}]),
        send_device_command=AsyncMock(),
    )
    use_action = await native.supports_lease_action(client, 31)
    await native.renew(client, 31, use_action)
    client.write_attribute.assert_awaited_once_with(31, native.PREFIX + "0", 10)
    client.send_device_command.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, "4", True, -1, 2**32])
async def test_unverified_build_does_not_select_either_protocol(version):
    flow, client = flow_with_node()
    client.read_attribute.return_value = identity(version)
    client.write_attribute = AsyncMock()
    result = await flow.async_step_user({"node": "fabric/31"})
    assert result["errors"] == {"base": "lease_unsupported"}
    client.send_device_command.assert_not_awaited()
    client.write_attribute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply", [None, [], [None], [{"Status": False}], [{"Status": 0x86}]]
)
async def test_unconfirmed_lease_never_triggers_a_second_command(reply):
    client = SimpleNamespace(
        write_attribute=AsyncMock(return_value=reply), send_device_command=AsyncMock()
    )
    with pytest.raises(native.LeaseUnsupported):
        await native.renew(client, 31, use_action=False)
    client.send_device_command.assert_not_awaited()


@pytest.mark.asyncio
async def test_lease_repair_backs_off_and_clears_after_firmware_upgrade(lease_clock):
    runtime = Runtime.__new__(Runtime)
    runtime.hass = SimpleNamespace(state=CoreState.running)
    runtime.node = 31
    runtime.status = None
    runtime.issue, runtime.clear_issue = Mock(), Mock()
    client = SimpleNamespace(
        read_attribute=AsyncMock(
            side_effect=[
                identity(3),
                identity(),
            ]
        ),
        write_attribute=AsyncMock(side_effect=InvalidArguments("Unknown cluster")),
        send_device_command=AsyncMock(),
        get_node=Mock(side_effect=[board(version=3), board(version=5)]),
    )
    runtime.client = Mock(return_value=client)
    lease_clock.stop_after = 2
    with pytest.raises(asyncio.CancelledError):
        await runtime._lease_loop()
    runtime.issue.assert_called_once()
    assert runtime.issue.call_args.args[0] == "lease"
    runtime.clear_issue.assert_called_once_with("lease")
    assert lease_clock.delays == [60, 4]
    assert (
        client.write_attribute.await_count
        == client.send_device_command.await_count
        == 1
    )


@pytest.mark.asyncio
async def test_stale_version_on_downgrade_rejected_then_vendor_lease_negotiated(
    lease_clock,
):
    runtime = Runtime.__new__(Runtime)
    runtime.hass = SimpleNamespace(state=CoreState.running)
    runtime.node = 31
    runtime.issue, runtime.clear_issue = Mock(), Mock()
    err = SDKStackError("UnsupportedCluster")
    err.status = 0xC3
    client = SimpleNamespace(
        read_attribute=AsyncMock(side_effect=[identity(5), identity(4)]),
        send_device_command=AsyncMock(side_effect=[None, err]),
        write_attribute=AsyncMock(return_value=[{"Status": 0}]),
        get_node=Mock(return_value=board(version=5)),
    )
    runtime.client = Mock(return_value=client)
    lease_clock.stop_after = 3
    with pytest.raises(asyncio.CancelledError):
        await runtime._lease_loop()
    assert client.read_attribute.await_count == 2
    assert client.send_device_command.await_count == 2
    client.write_attribute.assert_awaited_once_with(31, native.PREFIX + "0", 10)
    runtime.issue.assert_called_once()
    assert lease_clock.delays == [4, 60, 4]


@pytest.mark.asyncio
async def test_live_identity_change_prevents_invoking_another_products_action():
    flow, client = flow_with_node()
    client.read_attribute.return_value = {**identity(), "0/40/3": "Other product"}
    client.write_attribute = AsyncMock()
    result = await flow.async_step_user({"node": "fabric/31"})
    assert result["errors"] == {"base": "lease_unsupported"}
    client.send_device_command.assert_not_awaited()
    client.write_attribute.assert_not_awaited()


@pytest.mark.asyncio
async def test_unsupported_command_is_actionable_but_busy_write_is_transient():
    err = SDKStackError("UnsupportedCommand")
    err.status = 0x81
    client = SimpleNamespace(
        send_device_command=AsyncMock(side_effect=err),
        write_attribute=AsyncMock(return_value=[{"Status": 0x9C}]),
    )
    with pytest.raises(native.LeaseUnsupported):
        await native.renew(client, 31, use_action=True)
    with pytest.raises(ReflexError) as busy:
        await native.renew(client, 31, use_action=False)
    assert not isinstance(busy.value, native.LeaseUnsupported)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [0x7E, 0x8B, 0x9C, None])
async def test_failed_named_action_never_uses_another_transport(status):
    flow, client = flow_with_node()
    err = SDKStackError("Refused or unavailable")
    err.status = status
    client.send_device_command.side_effect = err
    client.write_attribute = AsyncMock()
    result = await flow.async_step_user({"node": "fabric/31"})
    assert result["errors"] == {
        "base": "lease_unsupported" if status in (0x7E, 0x8B) else "matter_unavailable"
    }
    client.write_attribute.assert_not_awaited()


@pytest.mark.asyncio
async def test_upgrade_from_working_legacy_transport_selects_named_action(monkeypatch):
    runtime = Runtime.__new__(Runtime)
    runtime.hass = SimpleNamespace(state=CoreState.running)
    runtime.node = 31
    runtime.clear_issue = Mock()
    client = SimpleNamespace(
        read_attribute=AsyncMock(side_effect=[identity(4), identity(5)]),
        write_attribute=AsyncMock(return_value=[{"Status": 0}]),
        send_device_command=AsyncMock(),
        get_node=Mock(side_effect=[board(version=4), board(version=5)]),
    )
    runtime.client = Mock(return_value=client)
    monkeypatch.setattr(
        asyncio, "sleep", AsyncMock(side_effect=[None, asyncio.CancelledError()])
    )
    with pytest.raises(asyncio.CancelledError):
        await runtime._lease_loop()
    client.write_attribute.assert_awaited_once_with(31, native.PREFIX + "0", 10)
    client.send_device_command.assert_awaited_once()


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
        read_attribute=AsyncMock(return_value=identity()),
        send_device_command=AsyncMock(),
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
        config_entries=SimpleNamespace(
            async_loaded_entries=lambda domain: [entry] if domain == "matter" else []
        )
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


@pytest.mark.asyncio
async def test_unsupported_lease_cannot_be_bypassed_by_rules_fallback(monkeypatch):
    flow, client = flow_with_node()
    # Stale registry data still describes the newer board.
    client.read_attribute.return_value = identity(3)
    client.write_attribute = AsyncMock(side_effect=InvalidArguments("Unknown cluster"))
    probe = AsyncMock()
    monkeypatch.setattr(native, "probe", probe)
    result = await flow.async_step_user({"node": "fabric/31"})
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "lease_unsupported"}
    probe.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconfigure_verifies_esphome_board_and_preserves_identity(monkeypatch):
    flow, _ = flow_with_node()
    flow.context = {"source": "reconfigure"}
    entry = SimpleNamespace(
        data={
            "matter_entry_id": "fabric",
            "node_id": 31,
            "fabric": 42,
            "mode": "http",
            "key": "01" * 32,
        }
    )
    flow._get_reconfigure_entry = Mock(return_value=entry)
    flow.async_update_reload_and_abort = Mock()
    status = AsyncMock()
    api = Mock(return_value=SimpleNamespace(status=status))
    monkeypatch.setattr(config_flow, "ESPHomeChannel", api)
    await flow.async_step_reconfigure({"esphome_entry_id": "bureau"})
    status.assert_awaited_once_with(None)
    api.assert_called_once_with(flow.hass, 31, 42, "bureau")
    flow.async_update_reload_and_abort.assert_called_once_with(
        entry,
        data={
            "matter_entry_id": "fabric",
            "node_id": 31,
            "fabric": 42,
            "mode": "esphome",
            "esphome_entry_id": "bureau",
        },
    )
