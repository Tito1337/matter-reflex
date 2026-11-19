"""Event-driven status using the actual MatterClient event dispatch contract."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from matter_server.client import MatterClient
from matter_server.common.models import EventType

from custom_components.matter_reflex import native
from custom_components.matter_reflex import status as status_module
from custom_components.matter_reflex.const import CLUSTER, VENDOR
from custom_components.matter_reflex.status import StatusMonitor
from custom_components.matter_reflex.transport import ReflexError


def snapshot(count=1, active=False, digest="a" * 64):
    return {
        "node_id": "000000000000001f",
        "executions": count,
        "hash": digest,
        "active": active,
    }


@pytest.fixture(autouse=True)
def local_timer(monkeypatch):
    monkeypatch.setattr(
        status_module, "async_track_time_interval", Mock(return_value=lambda: None)
    )


def make_client():
    node = SimpleNamespace(
        available=True,
        node_data=SimpleNamespace(
            attributes={
                "0/40/2": VENDOR,
                "0/40/3": "Matter Reflex",
                "1/29/1": [CLUSTER],
                "2/29/1": [6],
            }
        ),
    )
    client = MatterClient("ws://unused.invalid/ws", Mock())
    client.connection._ws_client = SimpleNamespace(closed=False)
    client._nodes[31] = node
    return client


def setup(channel=None):
    client = make_client()
    entry = SimpleNamespace(async_on_state_change=Mock(return_value=Mock()))
    rt = SimpleNamespace(
        node=31,
        entry=SimpleNamespace(
            data={"matter_entry_id": "matter"}, async_on_unload=Mock()
        ),
        hass=SimpleNamespace(
            config_entries=SimpleNamespace(async_get_entry=lambda _: entry)
        ),
        target_hash="a" * 64,
        status=None,
        acl_synced=True,
        client=Mock(return_value=client),
        channel=channel,
        native_status_readable=True,
        notify=Mock(),
        dirty=asyncio.Event(),
        read_status=AsyncMock(return_value=snapshot()),
    )
    monitor = StatusMonitor(rt)
    monitor.start()
    return monitor, rt, client, entry.async_on_state_change.call_args.args[0]


def report(client, index, value, node=31):
    client._signal_event(
        EventType.ATTRIBUTE_UPDATED, value, node, native.PREFIX + str(index)
    )


@pytest.mark.asyncio
async def test_pushed_values_and_lifecycle_never_replay_a_cached_snapshot():
    monitor, rt, client, entry_changed = setup()
    await monitor.refresh(client)
    rt.dirty.clear()
    report(client, 3, True)
    assert rt.status == snapshot(active=True) and not monitor.confirmed
    report(client, 4, 37)
    report(client, 2, "f" * 64)
    assert rt.status == snapshot(37, True, "f" * 64) and rt.dirty.is_set()
    assert monitor.confirmed
    report(client, 2, "", node=99)
    assert rt.status["hash"] == "f" * 64
    # NODE_UPDATED is a full cached interview, not a fresh diagnostic read.
    node = client.get_node(31)
    node.node_data.attributes[native.PREFIX + "4"] = 0
    client._signal_event(EventType.NODE_UPDATED, node, 31)
    assert rt.status["executions"] == 37 and rt.read_status.await_count == 1
    node.available = False
    client._signal_event(EventType.NODE_UPDATED, node, 31)
    assert rt.status is None and not monitor.online
    report(client, 4, 100)
    assert rt.status is None
    replacement = make_client()
    rt.client.return_value = replacement
    entry_changed()
    assert rt.status is None and monitor.client is replacement
    await monitor.refresh(replacement)
    report(client, 4, 800)
    assert rt.status == snapshot()  # old client callbacks were detached
    report(replacement, 3, True)
    assert rt.status == snapshot(active=True)
    replacement._signal_event(EventType.NODE_REMOVED, 31, 31)
    assert monitor.removed and rt.status is None


@pytest.mark.asyncio
async def test_change_during_delayed_http_read_is_preserved_and_disconnect_invalidates_result():
    monitor, rt, client, _ = setup(channel=object())
    await monitor.refresh(client)
    entered, release = asyncio.Event(), asyncio.Event()

    async def slow(_):
        entered.set()
        await release.wait()
        return snapshot(2)

    rt.read_status.side_effect = slow
    pending = asyncio.create_task(monitor.refresh(client))
    await entered.wait()
    report(client, 4, 3)
    release.set()
    assert (await pending)["executions"] == 3
    entered.clear()
    release.clear()
    pending = asyncio.create_task(monitor.refresh(client))
    await entered.wait()
    client._signal_event(EventType.SERVER_SHUTDOWN)
    release.set()
    with pytest.raises(ReflexError, match="connection changed"):
        await pending
    assert rt.status is None


@pytest.mark.asyncio
async def test_confirmed_stream_has_no_steady_reads(monkeypatch):
    monitor, rt, client, _ = setup()
    monkeypatch.setattr(status_module, "UNCONFIRMED_INTERVAL", 0.01)
    task = asyncio.create_task(monitor.run())
    try:
        async with asyncio.timeout(1):
            while rt.status is None:
                await asyncio.sleep(0)
        report(client, 3, True)
        report(client, 2, "f" * 64)
        report(client, 4, 1)
        await asyncio.sleep(0.04)
        rt.read_status.assert_awaited_once()
        assert rt.status["active"] is True
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_unobservable_board_is_refreshed_slowly_and_failed_status_is_unknown(
    monkeypatch,
):
    monitor, rt, client, _ = setup(channel=object())
    rt.native_status_readable = False
    rt.read_status.side_effect = [snapshot(), snapshot(2), TimeoutError()]
    monkeypatch.setattr(status_module, "UNCONFIRMED_INTERVAL", 0.01)
    task = asyncio.create_task(monitor.run())
    try:
        async with asyncio.timeout(1):
            while rt.status is None:
                await asyncio.sleep(0)
            report(client, 4, 1)  # even a partial report cannot prove native reads work
            while rt.read_status.await_count < 3 or rt.status is not None:
                await asyncio.sleep(0.001)
        assert not monitor.confirmed
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_unsupported_or_removed_node_not_resurrected_by_cached_http(monkeypatch):
    monitor, rt, client, _ = setup(channel=object())
    client._signal_event(EventType.NODE_REMOVED, 31, 31)
    task = asyncio.create_task(monitor.run())
    try:
        await asyncio.sleep(0)
        rt.read_status.assert_not_awaited()
        assert rt.status is None
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_silent_socket_loss_and_same_client_snapshot_replacement():
    monitor, rt, client, _ = setup()
    await monitor.refresh(client)
    report(client, 3, True)
    client.connection._ws_client.closed = True
    monitor.bind()
    assert rt.status is None and not monitor.online
    client.connection._ws_client.closed = False
    monitor.bind()
    assert monitor.online and not monitor.confirmed
    await monitor.refresh(client)
    report(client, 4, 5)
    client._nodes[31] = make_client().get_node(31)
    monitor.bind()
    assert rt.status is None and monitor.online and not monitor.reported_fields
    rt.read_status.assert_awaited()
    assert rt.read_status.await_count == 2  # Local lifecycle checks cause no I/O.


@pytest.mark.asyncio
async def test_partial_native_reporting_refreshes_silent_fields(monkeypatch):
    monitor, rt, client, _ = setup()
    rt.read_status.side_effect = [snapshot(), snapshot(76, True)]
    monkeypatch.setattr(status_module, "UNCONFIRMED_INTERVAL", 0.01)
    task = asyncio.create_task(monitor.run())
    try:
        async with asyncio.timeout(1):
            while rt.status is None:
                await asyncio.sleep(0)
            report(client, 3, True)
            while rt.status["executions"] != 76:
                await asyncio.sleep(0.001)
        assert monitor.reported_fields == {"active"} and not monitor.confirmed
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_invalid_snapshot_uses_normal_transport_error_handling():
    client = SimpleNamespace(read_attribute=AsyncMock(return_value=None))
    with pytest.raises(ReflexError, match="invalid status snapshot"):
        await native.read_status(client, 31)
