"""Ownership, HA editor discovery and per-run reporting in direct mode."""

import asyncio
from collections import deque
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant import loader
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import condition, entity_registry, label_registry
from homeassistant.helpers.condition import ConditionConfig

from custom_components.matter_reflex import ownership
from custom_components.matter_reflex.activity import Activity
from custom_components.matter_reflex.compiler import ActivityRule
from custom_components.matter_reflex.condition import AdvancedHandlers
from custom_components.matter_reflex.runtime import Runtime
from custom_components.matter_reflex.transport import JournalUnsupported
from custom_components.matter_reflex.wire import Rule


@pytest.mark.asyncio
async def test_editor_discovers_fields_and_runs_registered_mode(tmp_path):
    (tmp_path / "custom_components").symlink_to(Path("custom_components").resolve())
    hass = HomeAssistant(str(tmp_path))
    loader.async_setup(hass)
    await condition.async_setup(hass)
    config = {
        "condition": "matter_reflex.mode",
        "options": {"mode": "fallback", "esp": "test"},
    }
    validated = await condition.async_validate_condition_config(hass, config)
    checker = await condition.async_from_config(hass, validated)
    try:
        assert checker.async_check()
        descriptions = await condition.async_get_all_descriptions(hass)
        fields = descriptions["matter_reflex.mode"]["fields"]
        assert fields["mode"]["selector"]["select"]["options"] == ["direct", "fallback"]
        assert (
            fields["esp"]["selector"]["config_entry"]["integration"] == "matter_reflex"
        )
        assert "degraded_handler" in descriptions["matter_reflex.handlers"]["fields"]
    finally:
        checker.async_unload()


@pytest.mark.parametrize(
    ("normal", "degraded", "healthy", "expired"),
    [
        ("esp", "ha", False, True),
        ("ha", "esp", True, True),
        ("esp", "none", False, False),
        ("ha", "none", True, True),
        ("none", "ha", False, True),
        ("none", "esp", False, False),
    ],
)
@pytest.mark.asyncio
async def test_ha_checks_ownership_at_trigger_time(
    monkeypatch, normal, degraded, healthy, expired
):
    rt = Runtime.__new__(Runtime)
    monkeypatch.setattr(ownership, "monotonic", lambda: 0)
    row = ActivityRule(
        "automation.test",
        Rule(1, 5, 2, 6, 1, 7, 1, 0, direct=True),
        "light.test",
        "event.button",
    )
    rt.monitor = SimpleNamespace(ready_hash="verified", ready_mask=1, mask_known=True)
    rt.activity = SimpleNamespace(image_hash="verified", rules={1: row})
    entry = SimpleNamespace(
        state=ConfigEntryState.LOADED, domain="matter_reflex", runtime_data=rt
    )
    rt.hass = SimpleNamespace(
        data={},
        state=CoreState.running,
        config_entries=SimpleNamespace(async_get_entry=lambda _: entry),
    )
    reg = SimpleNamespace(labels={"label-123"})
    monkeypatch.setattr(
        entity_registry,
        "async_get",
        lambda _: SimpleNamespace(async_get={"automation.test": reg}.get),
    )
    monkeypatch.setattr(
        label_registry,
        "async_get",
        lambda _: SimpleNamespace(
            async_get_label_by_name={
                "reflex": SimpleNamespace(label_id="label-123")
            }.get
        ),
    )
    variables = {
        "this": {"entity_id": "automation.test"},
        "trigger": {"entity_id": "event.button"},
    }
    options = {
        "esp": "esp-entry",
        "normal_handler": normal,
        "degraded_handler": degraded,
    }
    gate = AdvancedHandlers(rt.hass, ConditionConfig(options=options))
    await gate.async_setup()
    try:
        monkeypatch.setattr(ownership, "monotonic", lambda: 9.99)
        assert gate.async_check(variables=variables) is healthy
        monkeypatch.setattr(ownership, "monotonic", lambda: 10)
        assert gate.async_check(variables=variables) is expired
        ownership.lease_for(rt.hass, "esp-entry").renew()
        assert gate.async_check(variables=variables) is healthy
        if degraded == "ha":
            entry.state = ConfigEntryState.SETUP_RETRY
            assert not gate.async_check(variables=variables)
            monkeypatch.setattr(ownership, "monotonic", lambda: 20)
            assert gate.async_check(variables=variables)
            entry.state = ConfigEntryState.LOADED
            ownership.lease_for(rt.hass, "esp-entry").renew()
        if normal == "esp" and degraded == "ha":
            rt.monitor.ready_hash = None
            lease = ownership.lease_for(rt.hass, "esp-entry")
            lease.start()
            assert not gate.async_check(variables=variables)
            monkeypatch.setattr(ownership, "monotonic", lambda: 29)
            lease.renew()
            monkeypatch.setattr(ownership, "monotonic", lambda: 30)
            assert gate.async_check(variables=variables)
            rt.monitor.ready_hash = "verified"
            # A new gesture on the same event entity remains HA-owned.
            trigger = variables["trigger"]
            trigger["to_state"] = SimpleNamespace(
                attributes={"event_type": "multi_press_2"}
            )
            assert gate.async_check(variables=variables)
            trigger["to_state"].attributes["event_type"] = "multi_press_1"
            assert not gate.async_check(variables=variables)
            rt.monitor.ready_mask = 0
            assert gate.async_check(variables=variables)
            rt.monitor.ready_mask = 1
            rt.monitor.ready_hash = "old"
            assert gate.async_check(variables=variables)
            rt.monitor.ready_hash = "verified"
            assert not gate.async_check(variables=variables)
        reg.labels.clear()
        assert gate.async_check(variables=variables)
        reg.labels.add("label-123")
        assert gate.async_check(variables=variables) is healthy
    finally:
        gate.async_unload()


@pytest.mark.parametrize("direct", [True, False])
def test_activity_combines_target_results_and_deduplicates_reconnect(
    monkeypatch, direct
):
    from custom_components.matter_reflex import activity as module

    records = Mock()
    monkeypatch.setattr(module, "async_log_entry", records)
    activity = Activity.__new__(Activity)
    activity.runtime = SimpleNamespace(
        dirty=asyncio.Event(),
        hass=SimpleNamespace(states=SimpleNamespace(get=lambda _: None)),
    )
    activity.store = Mock()
    activity.image_hash = None
    activity.rules = {}
    activity.seen = deque(maxlen=128)
    activity.pending = {}
    activity.wake = asyncio.Event()
    rules = {
        i: ActivityRule(
            "automation.lights",
            Rule(i, 5, 2, 6, 1, i, 1, 0, direct=direct),
            f"light.lamp_{i}",
            "event.button",
        )
        for i in (1, 2)
    }

    activity.configure("verified", rules)

    def snapshot(rows, image="verified"):
        return {
            "hash": image,
            "journal": "".join(
                f"{run:016x}{rule:08x}{status:04x}" for run, rule, status in rows
            ),
        }

    rows = [(13, 1, 0), (14, 1, 0x101), (13, 2, 0x102)]
    activity.record(snapshot(rows[:2]))
    activity.record(snapshot(rows, "wrong-image"))
    records.assert_not_called()
    # The first target has already rotated out of the board journal.
    activity.configure(
        "verified",
        {i: replace(row, entity_id="automation.renamed") for i, row in rules.items()},
    )
    activity.record(snapshot(rows[2:]))
    activity.record(snapshot(rows))
    records.assert_called_once()
    message = records.call_args.args[2]
    assert ("Direct reflex" if direct else "Fallback reflex") in message
    assert "lamp_1 (accepted)" in message and "lamp_2 (transport error)" in message
    assert records.call_args.args[-1] == "automation.renamed"
    activity.record(snapshot(rows + [(14, 2, 0)]))
    assert records.call_count == 2
    activity.record(snapshot([(200, 1, 0)]))
    activity.configure("next", activity.rules)
    activity.record(snapshot([(200, 2, 0)], "next"))
    assert records.call_count == 2


@pytest.mark.asyncio
async def test_unloaded_entry_grace_starts_before_first_trigger(monkeypatch, tmp_path):
    hass = HomeAssistant(str(tmp_path))
    hass.state = CoreState.running
    monkeypatch.setattr(ownership, "monotonic", lambda: 100)
    lease = ownership.lease_for(hass, "retrying")
    assert not lease.expired(hass)
    monkeypatch.setattr(ownership, "monotonic", lambda: 111)
    assert ownership.lease_for(hass, "retrying").expired(hass)
    lease.renew()
    assert not lease.expired(hass)


@pytest.mark.asyncio
async def test_legacy_activity_reprobes_only_after_firmware_or_connection_change(
    tmp_path,
):
    hass = HomeAssistant(str(tmp_path))
    monitor = SimpleNamespace(generation=1, software_version=5, confirmed=False)
    rt = SimpleNamespace(
        hass=hass,
        entry=SimpleNamespace(entry_id="esp"),
        monitor=monitor,
        status={"executions": 10},
    )
    activity = Activity(rt)
    activity.configure(
        "current",
        {
            1: ActivityRule(
                "automation.lamp",
                Rule(1, 1, 1, 1, 0, 2, 1, 1),
                "light.lamp",
                "event.button",
            )
        },
    )
    activity.read = AsyncMock(side_effect=JournalUnsupported())
    task = asyncio.create_task(activity.run())

    async def wait_for_reads(count):
        async with asyncio.timeout(3):
            while activity.read.await_count < count:
                await asyncio.sleep(0)

    try:
        await wait_for_reads(1)
        activity.wake.set()
        await asyncio.sleep(0)
        assert activity.read.await_count == 1
        monitor.software_version += 1
        activity.wake.set()
        await wait_for_reads(2)
        monitor.generation += 1
        activity.wake.set()
        await wait_for_reads(3)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.parametrize(
    "event,kind,state,expected",
    [
        (7, "contact", "on", True),
        (7, "leak", "on", False),
        (10, "occupancy", "on", True),
        (11, "temperature", "69.8", True),
        (11, "temperature", "68", False),
    ],
)
def test_installed_sensor_trigger_matches_physical_edge(event, kind, state, expected):
    rule = ActivityRule(
        "automation.lamp",
        Rule(1, 1, 1, event, 0, 2, 1, 1, threshold=2000),
        "light.lamp",
        "sensor.source",
        kind,
    )

    def sample(value):
        return SimpleNamespace(state=value, attributes={"unit_of_measurement": "°F"})

    assert (
        rule.matches_trigger(
            {
                "entity_id": "sensor.source",
                "from_state": sample("67"),
                "to_state": sample(state),
            }
        )
        is expected
    )
