"""Contract and safety checks for the HA half of Matter Reflex."""

import asyncio
import copy
import hashlib
import hmac
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import CoreState

from custom_components.matter_reflex.acl import ACL, AclManager, plan
from custom_components.matter_reflex.runtime import Runtime
from custom_components.matter_reflex.transport import Channel, ReflexError, signature

ADMIN = {"1": 5, "2": 2, "3": [112233], "4": None, "254": 2}
SUBJECT = 0x100000000000011


class MemoryStore:
    def __init__(self):
        self.value = None

    async def async_load(self):
        return copy.deepcopy(self.value)

    async def async_save(self, value):
        self.value = copy.deepcopy(value)


class Device:
    def __init__(self):
        self.rows = [copy.deepcopy(ADMIN)]
        self.server_info = SimpleNamespace(schema_version=14)
        self.writes = 0

    def get_nodes(self):
        return [SimpleNamespace(node_id=15)]

    async def read(self, node, attribute_paths, fabric_filtered):
        assert fabric_filtered
        return SimpleNamespace(
            attributes={
                ACL: copy.deepcopy(self.rows),
                "0/62/5": 2,
                "0/31/3": 3,
                "0/31/4": 3,
            }
        )

    async def write_attribute(self, node, path, value):
        assert node == 15 and path == ACL
        self.writes += 1
        self.rows = copy.deepcopy(value)
        return [{"Status": 0}]


@pytest.mark.asyncio
async def test_acl_idempotent_then_pruned_after_restart():
    client, store = Device(), MemoryStore()
    acl = AclManager(store, SUBJECT)
    wanted = {15: {(1, 6, 3), (1, 8, 3)}}
    await acl.load()
    await acl.reconcile(client, wanted, False)
    assert client.rows[0] == ADMIN
    assert client.rows[1]["3"] == [SUBJECT]
    assert client.rows[1]["4"] == [
        {"0": 6, "1": 1, "2": None},
        {"0": 8, "1": 1, "2": None},
    ]
    await acl.reconcile(client, wanted, True)
    assert client.writes == 1
    restarted = AclManager(store, SUBJECT)
    await restarted.load()
    await restarted.reconcile(client, {}, True)
    assert client.rows == [ADMIN] and client.writes == 2 and store.value == {}


@pytest.mark.asyncio
async def test_user_owned_equivalent_acl_never_adopted():
    client, store = Device(), MemoryStore()
    client.rows += [{"1": 3, "2": 2, "3": [SUBJECT], "4": None, "254": 2}]
    acl = AclManager(store, SUBJECT)
    await acl.reconcile(client, {15: {(1, 6, 3)}}, True)
    await acl.reconcile(client, {}, True)
    assert client.writes == 0 and store.value is None


@pytest.mark.asyncio
async def test_pending_write_resumed_without_duplicate():
    client, store = Device(), MemoryStore()
    acl = AclManager(store, SUBJECT)
    write = client.write_attribute

    async def committed_then_disconnected(*args):
        await write(*args)
        raise TimeoutError

    client.write_attribute = committed_then_disconnected
    with pytest.raises(TimeoutError):
        await acl.reconcile(client, {15: {(1, 59, 1)}}, False)
    assert store.value["15"]["pending"]
    restarted = AclManager(store, SUBJECT)
    await restarted.load()
    await restarted.reconcile(client, {15: {(1, 59, 1)}}, False)
    assert client.writes == 1 and "pending" not in store.value["15"]


@pytest.mark.asyncio
async def test_concurrent_change_aborts_before_write():
    client, acl = Device(), AclManager(MemoryStore(), SUBJECT)
    initial = await acl.snapshot(client, 15)
    changed = ([dict(ADMIN, **{"3": [123]})], *initial[1:])
    acl.snapshot = AsyncMock(side_effect=[initial, changed])
    with pytest.raises(ReflexError, match="Concurrent"):
        await acl.reconcile(client, {15: {(1, 6, 3)}}, True)
    assert client.writes == 0
    assert acl.store.value == {}


@pytest.mark.parametrize(
    "rows,limit",
    [
        ([], 3),
        ([dict(ADMIN, **{"3": [SUBJECT]})], 3),
        ([dict(ADMIN, **{"254": 3})], 3),
        ([dict(ADMIN, **{"4": []})], 3),
        ([ADMIN], 1),
        ([ADMIN, dict(ADMIN, **{"3": [456]})], 3),
    ],
)
def test_acl_refuses_ambiguous_admin_or_exhaustion(rows, limit):
    with pytest.raises(ReflexError):
        plan(rows, [], SUBJECT, {(1, 6, 3)}, 2, 3, limit, True)


def test_hmac_protocol_separates_paths_and_response():
    key, nonce, body = bytes(range(32)), "ab" * 16, b"10"
    mac = signature(key, "REQ", nonce, "/v1/lease", body=body)
    message = f"REQ\n{nonce}\n/v1/lease\n{hashlib.sha256(body).hexdigest()}"
    assert mac == hmac.new(key, message.encode(), hashlib.sha256).hexdigest()
    assert mac != signature(key, "REQ", nonce, "/v1/rules", body=body)
    assert mac != signature(key, "RES", nonce, "/v1/lease", "200", body=body)


@pytest.mark.asyncio
async def test_absent_old_node_does_not_block_remaining_cleanup():
    client, store = Device(), MemoryStore()
    acl = AclManager(store, SUBJECT)
    await acl.reconcile(client, {15: {(1, 6, 3)}}, False)
    await acl.record(3, {"owned": [copy.deepcopy(client.rows[1])]})
    previous = copy.deepcopy(store.value["3"])
    await acl.reconcile(client, {}, True)
    assert client.rows == [ADMIN]
    assert store.value == {"3": previous}


@pytest.mark.asyncio
async def test_final_snapshot_after_persisting_intent():
    client, store = Device(), MemoryStore()
    acl = AclManager(store, SUBJECT)
    persist = store.async_save

    async def simulate_external_edit_at_save(value):
        if value.get("15", {}).get("pending"):
            client.rows.append({"1": 1, "2": 2, "3": [200], "4": None, "254": 2})
        await persist(value)

    store.async_save = simulate_external_edit_at_save
    with pytest.raises(ReflexError, match="Concurrent ACL"):
        await acl.reconcile(client, {15: {(1, 6, 3)}}, False)
    assert client.writes == 0
    assert store.value == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        ReflexError("Reflex HTTP status 404"),
        ReflexError("Unauthenticated Reflex response"),
        OSError(),
    ],
)
async def test_cached_address_can_move_to_another_thread_address(failure):
    channel = Channel.__new__(Channel)
    channel.address = "fd00::1"
    channel.node_id = 12
    channel._exchange = AsyncMock(side_effect=[failure, b"reply"])
    client = SimpleNamespace(
        get_node_ip_addresses=AsyncMock(
            return_value=["fd00::1", "192.0.2.1", "::1", "fd00::2"]
        )
    )
    assert await channel.request(client, "status") == b"reply"
    assert channel.address == "fd00::2" and channel._exchange.await_count == 2
    client.get_node_ip_addresses.assert_awaited_once_with(12, prefer_cache=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [CoreState.starting, CoreState.stopping])
async def test_no_lease_or_sync_outside_running(state, monkeypatch):
    runtime = Runtime.__new__(Runtime)
    runtime.hass = SimpleNamespace(state=state, is_running=True)
    runtime.client = Mock(
        side_effect=AssertionError("Contacted Matter during startup/shutdown")
    )
    await runtime.sync()
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await runtime._lease_loop()
    runtime.client.assert_not_called()


@pytest.mark.asyncio
async def test_matter_disconnect_cancellation_retries_but_unload_stops(monkeypatch):
    import asyncio

    from custom_components.matter_reflex.runtime import Runtime

    runtime = Runtime.__new__(Runtime)
    tries = 0

    async def worker():
        nonlocal tries
        tries += 1
        if tries == 1:
            pending = asyncio.get_running_loop().create_future()
            pending.cancel()
            await pending

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    await runtime._maintain_task(worker)
    assert tries == 2

    started = asyncio.Event()

    async def pending_read():
        started.set()
        await asyncio.Future()

    task = asyncio.create_task(runtime._maintain_task(pending_read))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_cover_acl_exact_target_and_cleanup():
    client, store = Device(), MemoryStore()
    acl = AclManager(store, SUBJECT)
    await acl.reconcile(client, {15: {(7, 0x0102, 3)}}, True)
    assert client.rows == [
        ADMIN,
        {
            "1": 3,
            "2": 2,
            "3": [SUBJECT],
            "4": [{"0": 0x0102, "1": 7, "2": None}],
            "254": 2,
        },
    ]
    await acl.reconcile(client, {}, True)
    assert client.rows == [ADMIN] and store.value == {}


@pytest.mark.parametrize("grant", [(7, 0x0102, 1), (7, 0x0102, 4), (7, 0x0103, 3)])
def test_cover_acl_rejects_other_clusters_or_privileges(grant):
    with pytest.raises(ReflexError, match="Unsupported ACL"):
        plan([ADMIN], [], SUBJECT, {grant}, 2, 3, 3, True)
