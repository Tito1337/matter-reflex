import pytest

from custom_components.matter_reflex.compiler import Endpoint, compile_automation
from custom_components.matter_reflex.wire import Rule, encode


def sample():
    return {
        "triggers": [
            {
                "trigger": "event.received",
                "target": {"entity_id": "event.remote_button"},
                "options": {"event_type": ["multi_press_2"]},
            }
        ],
        "actions": [
            {
                "action": "light.turn_on",
                "target": {"entity_id": "light.floor"},
                "data": {"brightness_pct": 70},
            }
        ],
    }


def lookup(name):
    return {
        "event.remote_button": Endpoint(
            0x123456789ABCDEF0, 2, event_types=("multi_press_2", "long_press")
        ),
        "light.floor": Endpoint(42, 1, dimmable=True),
    }[name]


def test_toggle_selection_is_per_target():
    endpoints = {
        "event.remote_button": lookup("event.remote_button"),
        "light.native": Endpoint(40, 1, light_power_ops=frozenset({14})),
        "light.legacy": Endpoint(41, 1, light_power_ops=frozenset({0, 1, 2})),
    }
    cfg = sample()
    cfg["actions"] = [
        {
            "action": "light.toggle",
            "target": {"entity_id": ["light.native", "light.legacy"]},
        }
    ]
    rows = compile_automation("mixed", cfg, endpoints.__getitem__)
    assert {row.target: row.op for row in rows} == {40: 14, 41: 2}


def test_pilot_wire():
    (row,) = compile_automation("pilot", sample(), lookup)
    assert (row.event, row.presses, row.op, row.value) == (6, 2, 3, 178)
    assert row.source == 0x123456789ABCDEF0
    vector = Rule(1, row.source, 2, 6, 2, 42, 1, 3, 178)
    assert encode([vector]).hex() == (
        "4d52465801010000"
        "00000001123456789abcdef000020602"
        "000000000000002a00010300b201fe00"
    )


@pytest.mark.parametrize(
    "data,expected",
    [
        ({"brightness_step_pct": -25}, (5, -25)),
        ({"brightness_step": -100}, (4, -100)),
        ({"brightness": 0}, (3, 0)),
    ],
)
def test_brightness(data, expected):
    config = sample()
    config["actions"][0]["data"] = data
    (rule,) = compile_automation("a", config, lookup)
    assert (rule.op, rule.value) == expected


@pytest.mark.parametrize(
    "change",
    [
        {"conditions": [{"condition": "sun"}]},
        {"actions": [{"action": "light.turn_off", "target": {"area_id": "bedroom"}}]},
        {"triggers": [{"trigger": "state", "entity_id": "event.remote_button"}]},
        {"variables": {"x": 1}},
    ],
)
def test_reject_whole_automation(change):
    with pytest.raises(ValueError):
        compile_automation("a", sample() | change, lookup)


def test_deterministic_and_limit():
    (rule,) = compile_automation("a", sample(), lookup)
    with pytest.raises(ValueError):
        encode([rule, rule])
    assert encode([]) == b"MRFX\x01\x00\x00\x00"


@pytest.mark.parametrize(
    "service,data,op,value",
    [
        ("open_cover", {}, 6, 0),
        ("close_cover", {}, 7, 0),
        ("stop_cover", {}, 8, 0),
        ("set_cover_position", {"position": 0}, 9, 0),
        ("set_cover_position", {"position": 27}, 9, 27),
        ("set_cover_position", {"position": 100}, 9, 100),
    ],
)
def test_cover_wire_keeps_ha_position(service, data, op, value):
    config = sample()
    config["actions"] = [
        {
            "action": f"cover.{service}",
            "target": {"entity_id": "cover.shade"},
            "data": data,
        }
    ]

    def resolve(name):
        return (
            Endpoint(42, 7, cover_ops=frozenset({op}))
            if name == "cover.shade"
            else lookup(name)
        )

    (rule,) = compile_automation("shade", config, resolve)
    assert (
        rule.target,
        rule.target_ep,
        rule.op,
        rule.value,
        rule.minimum,
        rule.maximum,
    ) == (42, 7, op, value, 1, 254)


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"position": False},
        {"position": 2.5},
        {"position": -1},
        {"position": 101},
        {"position": "50"},
        {"position": "{{ foo }}"},
        {"tilt_position": 5},
    ],
)
def test_reject_invalid_cover_position(data):
    config = sample()
    config["actions"] = [
        {
            "action": "cover.set_cover_position",
            "data": data,
            "target": {"entity_id": "cover.shade"},
        }
    ]
    with pytest.raises(ValueError):
        compile_automation("shade", config, lookup)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "features,accepted,has_cluster,success",
    [
        (4, [5], True, True),
        (0, [5], True, False),
        (4, [0, 1, 2], True, False),
        (4, [5], False, False),
        (128, [8], True, False),
    ],
)
async def test_cover_compile_checks_real_registry_and_acl(
    monkeypatch, features, accepted, has_cluster, success
):
    from types import SimpleNamespace

    from homeassistant.components.automation.const import DATA_COMPONENT
    from homeassistant.components.matter import helpers
    from homeassistant.helpers import entity_registry, label_registry

    from custom_components.matter_reflex.compiler import async_compile
    from custom_components.matter_reflex.wire import ROW

    def endpoint(index, clusters):
        return SimpleNamespace(
            endpoint_id=index,
            get_cluster=clusters.get,
            has_attribute=lambda cluster, attribute: (
                (cluster, attribute) == (0x102, 0xFFF9)
            ),
            get_attribute_value=lambda cluster, attribute: (
                accepted if (cluster, attribute) == (0x102, 0xFFF9) else None
            ),
        )

    def registered(domain, name, unique_id):
        return SimpleNamespace(
            entity_id=f"{domain}.{name}",
            domain=domain,
            unique_id=unique_id,
            platform="matter",
            config_entry_id="fabric",
            disabled_by=None,
            labels={"fallback_reflex"},
            supported_features=features,
            capabilities={"event_types": ["multi_press_2"]},
        )

    src, target, auto = (
        registered("event", "remote_button", "ref-2-remote-59-0"),
        registered("cover", "shade", "ref-7-cover-258-0"),
        registered("automation", "shade", "a"),
    )
    entries = {e.entity_id: e for e in (src, target, auto)}
    config = sample()
    config["actions"] = [
        {
            "action": "cover.set_cover_position",
            "data": {"position": 42},
            "target": {"entity_id": "cover.shade"},
        }
    ]
    nodes = [
        SimpleNamespace(node_id=21, endpoints={2: endpoint(2, {59: object()})}),
        SimpleNamespace(
            node_id=42,
            endpoints={
                7: endpoint(
                    7,
                    {0x102: object(), 6: object(), 8: object()} if has_cluster else {},
                )
            },
        ),
    ]
    client = SimpleNamespace(server_info=object(), get_nodes=lambda: nodes)
    runtime = SimpleNamespace(adapter=SimpleNamespace(matter_client=client))
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_get_entry=lambda _: SimpleNamespace(runtime_data=runtime)
        ),
        states=SimpleNamespace(
            get=lambda _: SimpleNamespace(state="on", attributes={})
        ),
        data={
            DATA_COMPONENT: SimpleNamespace(
                get_entity=lambda _: SimpleNamespace(raw_config=config)
            )
        },
    )
    monkeypatch.setattr(helpers, "get_device_id", lambda *_: "ref")
    monkeypatch.setattr(
        entity_registry,
        "async_get",
        lambda _: SimpleNamespace(entities=entries, async_get=entries.get),
    )
    monkeypatch.setattr(
        label_registry,
        "async_get",
        lambda _: SimpleNamespace(
            async_get_label_by_name={
                "reflex": SimpleNamespace(label_id="fallback_reflex")
            }.get
        ),
    )
    result = await async_compile(hass, "fabric")
    if success:
        assert not result.issues and result.permissions == {
            21: {(2, 59, 1)},
            42: {(7, 0x102, 3)},
        }
        assert ROW.unpack(result.image[8:])[7:9] == (9, 42)
    else:
        assert (
            auto.entity_id in result.issues
            and not result.permissions
            and result.image == encode([])
        )


@pytest.mark.parametrize(
    "name,flag,command,operation",
    [
        ("open_cover", 1, 0, 6),
        ("close_cover", 2, 1, 7),
        ("stop_cover", 8, 2, 8),
        ("set_cover_position", 4, 5, 9),
        ("set_cover_tilt_position", 128, 8, 10),
        ("open_cover_tilt", 16, 0, 11),
        ("close_cover_tilt", 32, 1, 12),
        ("stop_cover_tilt", 64, 2, 13),
    ],
)
def test_single_cover_feature_and_accepted_command(name, flag, command, operation):
    from custom_components.matter_reflex.compiler import (
        COVER_OPERATIONS,
        cover_operations,
    )

    assert (
        cover_operations(flag, [command])
        == {operation}
        == {COVER_OPERATIONS[f"cover.{name}"]}
    )
    assert cover_operations(0, [command]) == set()
    assert cover_operations(flag, []) == set()
