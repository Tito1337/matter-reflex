"""Compile registry-backed Matter outlets independently of the names assigned in HA."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from homeassistant.components.automation.const import DATA_COMPONENT
from homeassistant.components.matter import helpers
from homeassistant.helpers import entity_registry, label_registry
from matter_server.client.models.node import MatterNode
from matter_server.common.models import MatterNodeData

from custom_components.matter_reflex.compiler import async_compile
from custom_components.matter_reflex.const import VENDOR
from custom_components.matter_reflex.wire import ROW, encode


async def compile_plug(
    monkeypatch,
    *,
    service="toggle",
    types=(0x010A,),
    uid="MatterPlug-6-0",
    commands=(0, 1),
    has_onoff=True,
    platform="matter",
    disabled_by=None,
    category=None,
    reflex_node=99,
    conditions=None,
    tagged=True,
    entry_id=None,
    product="GRILLPLATS",
    vendor=4476,
    data=None,
):
    def node(node_id, attributes):
        now = datetime.now(UTC)
        return MatterNode(
            MatterNodeData(node_id, now, now, 1, True, attributes=attributes)
        )

    source = node(21, {"3/29/0": [{"0": 15, "1": 1}], "3/59/0": 2})
    attrs = {
        "0/40/2": vendor,
        "0/40/3": product,
        "1/29/0": [{"0": device_type, "1": 3} for device_type in types],
        "1/8/0": 125,
    }
    if product is None:
        del attrs["0/40/3"]
    if has_onoff:
        attrs["1/6/0"] = True
        if commands is not None:
            attrs["1/6/65529"] = list(commands)
    target = node(42, attrs)
    client = SimpleNamespace(server_info=object(), get_nodes=lambda: [source, target])
    runtime = SimpleNamespace(adapter=SimpleNamespace(matter_client=client))

    def registered(domain, name, key, **kwargs):
        return SimpleNamespace(
            entity_id=f"{domain}.{name}",
            domain=domain,
            unique_id=key,
            platform="matter",
            config_entry_id="fabric",
            disabled_by=None,
            labels={"reflex"},
            capabilities={"event_types": ["multi_press_1"]},
            **kwargs,
        )

    trigger = registered("event", "tester", "ref-3-remote-59-0")
    switch = registered("switch", "anything", f"ref-1-{uid}")
    switch.platform, switch.disabled_by = platform, disabled_by
    switch.entity_category = category
    automation = registered("automation", "socket", "slot")
    regs = {e.entity_id: e for e in (switch, trigger, automation)}
    cfg = {
        "triggers": [
            {
                "trigger": "event.received",
                "target": {"entity_id": trigger.entity_id},
                "options": {"event_type": "multi_press_1"},
            }
        ],
        "actions": [
            {
                "action": f"switch.{service}",
                "data": data or {},
                "target": {"entity_id": switch.entity_id},
            }
        ],
    }
    if conditions is not None:
        cfg["conditions"] = conditions
    if not tagged:
        automation.labels.clear()
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_get_entry=lambda _: SimpleNamespace(runtime_data=runtime)
        ),
        states=SimpleNamespace(
            get=lambda _: SimpleNamespace(state="on", attributes={})
        ),
        data={
            DATA_COMPONENT: SimpleNamespace(
                get_entity=lambda _: SimpleNamespace(raw_config=cfg)
            )
        },
    )
    monkeypatch.setattr(helpers, "get_device_id", lambda *_: "ref")
    monkeypatch.setattr(
        entity_registry,
        "async_get",
        lambda _: SimpleNamespace(entities=regs, async_get=regs.get),
    )
    monkeypatch.setattr(
        label_registry,
        "async_get",
        lambda _: SimpleNamespace(
            async_get_label_by_name=lambda _: SimpleNamespace(label_id="reflex")
        ),
    )
    return await async_compile(hass, "fabric", reflex_node, entry_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "device,uid", [(0x010A, "MatterPlug-6-0"), (0x010B, "MatterSwitch-6-0")]
)
@pytest.mark.parametrize(
    "service, expected", [("turn_off", 0), ("turn_on", 1), ("toggle", 2)]
)
async def test_outlet_absolute_wire_and_onoff_only_acl(
    monkeypatch, device, uid, service, expected
):
    result = await compile_plug(monkeypatch, types=(device,), uid=uid, service=service)
    assert result.issues == {}
    assert ROW.unpack(result.image[8:])[7:9] == (expected, 0)
    assert result.permissions == {21: {(3, 59, 1)}, 42: {(1, 6, 3)}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"types": (0x0100,), "uid": "MatterSwitch-6-0"},  # light or generic relay
        {"types": (0x0202,), "uid": "WindowCoveringReverseMotorDirection-258-23"},
        {
            "uid": "WindowCoveringReverseMotorDirection-258-23"
        },  # secondary control even on a plug
        {"uid": "MatterMuteToggle-6-0"},  # same primary attr but inverted semantics
        {"uid": "MatterPlug-6-1"},
        {"platform": "mqtt"},
        {"disabled_by": "user"},
        {"category": "config", "uid": "MatterSwitch-6-0"},
        {"category": "diagnostic"},
        {"product": "Matter Reflex", "vendor": VENDOR},
        {"product": None, "reflex_node": 42},
        {"commands": ()},  # a reported empty list is an unsupported action
        {"commands": (0, 2)},  # native Toggle alone cannot synthesize both absolutes
        {"has_onoff": False},
        {"service": "turn_on", "data": {"brightness": 12}},
    ],
)
async def test_non_power_or_unsupported_switch_is_excluded_with_repair(
    monkeypatch, overrides
):
    result = await compile_plug(monkeypatch, **overrides)
    assert "automation.socket" in result.issues
    assert result.permissions == {}
    assert result.image == encode([])


@pytest.mark.asyncio
async def test_unrelated_outlet_with_incomplete_basic_information(monkeypatch):
    result = await compile_plug(monkeypatch, product=None)
    assert result.permissions[42] == {(1, 6, 3)}
    assert not result.issues


@pytest.mark.asyncio
async def test_missing_command_report_does_not_compile_an_empty_image(monkeypatch):
    with pytest.raises(RuntimeError, match="Waiting for the Matter commands"):
        await compile_plug(monkeypatch, commands=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("tagged", [True, False])
async def test_direct_gate_replaces_label_and_selects_one_esp(monkeypatch, tagged):
    gate = {
        "condition": "matter_reflex.mode",
        "options": {"mode": "direct", "esp": "board-a"},
    }
    cfg = {"tagged": tagged, "conditions": [gate]}
    first = await compile_plug(monkeypatch, **cfg, entry_id="board-a")
    second = await compile_plug(monkeypatch, **cfg, entry_id="board-b")
    assert first.issues == second.issues == {}
    assert ROW.unpack(first.image[8:])[7] == 0x82
    assert next(iter(first.activity.values())).target == "switch.anything"
    assert second.image == encode([])
    unsafe = await compile_plug(
        monkeypatch,
        entry_id="board-a",
        tagged=False,
        conditions=[gate, {"condition": "template", "value_template": "false"}],
    )
    assert unsafe.image == encode([]) and "automation.socket" in unsafe.issues


@pytest.mark.asyncio
async def test_nested_ownership_condition_reports_repair(monkeypatch):
    nested = {
        "condition": "or",
        "conditions": [
            {
                "condition": "matter_reflex.mode",
                "options": {"mode": "direct", "esp": "board-a"},
            }
        ],
    }
    result = await compile_plug(
        monkeypatch, conditions=[nested], tagged=False, entry_id="board-a"
    )
    assert result.image == encode([])
    assert "top level" in result.issues["automation.socket"]
