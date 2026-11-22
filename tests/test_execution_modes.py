"""Ownership, HA editor discovery and per-run reporting in direct mode."""

import asyncio
from collections import deque
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

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
    rt.desired_rules = {1: row}
    rt.monitor = SimpleNamespace(ready_hash="verified", ready_mask=1)
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
            # The ESP is reachable but its image is absent, rejected or stale.
            rt.desired_rules.clear()
            assert gate.async_check(variables=variables)
            rt.desired_rules = {1: replace(row, row=replace(row.row, value=42))}
            assert gate.async_check(variables=variables)
            rt.desired_rules = {1: row}
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
