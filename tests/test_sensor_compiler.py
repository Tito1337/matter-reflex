"""Exercise attribute discovery and permissions using interviewed Matter endpoints."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from homeassistant.components.automation.const import DATA_COMPONENT
from homeassistant.components.matter import helpers
from homeassistant.helpers import entity_registry, label_registry
from matter_server.client.models.node import MatterNode
from matter_server.common.models import MatterNodeData

from custom_components.matter_reflex.acl import plan
from custom_components.matter_reflex.compiler import (
    Endpoint,
    async_compile,
    compile_automation,
)
from custom_components.matter_reflex.wire import EXTENSION, ROW, encode


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "device,key,cluster,domain,unit,criteria,expected",
    [
        (21, "ContactSensor", 69, "binary_sensor", "", {"to": "on"}, (7, 0)),
        (
            21,
            "ContactSensor",
            69,
            "binary_sensor",
            "",
            {"from": "on", "to": "off"},
            (8, 0),
        ),
        (67, "WaterLeakDetector", 69, "binary_sensor", "", {"to": "on"}, (8, 0)),
        (67, "WaterLeakDetector", 69, "binary_sensor", "", {"to": "off"}, (7, 0)),
        (263, "OccupancySensor", 1030, "binary_sensor", "", {"to": "on"}, (10, 0)),
        (263, "OccupancySensor", 1030, "binary_sensor", "", {"to": "off"}, (9, 0)),
        (770, "TemperatureSensor", 1026, "sensor", "°F", {"above": 31}, (11, -56)),
        (770, "TemperatureSensor", 1026, "sensor", "°C", {"below": -0.125}, (12, -12)),
        (770, "TemperatureSensor", 1026, "sensor", "K", {"below": 273.15}, (12, 0)),
        (775, "HumiditySensor", 1029, "sensor", "%", {"above": 55.123}, (13, 5512)),
        (775, "HumiditySensor", 1029, "sensor", "%", {"below": 55.123}, (14, 5513)),
        (67, "ContactSensor", 69, "binary_sensor", "", {"to": "on"}, None),
        (
            21,
            "ContactSensor",
            69,
            "binary_sensor",
            "",
            {"from": "on", "to": "on"},
            None,
        ),
        (21, "ContactSensor", 69, "binary_sensor", "", {"to": "on", "for": 1}, None),
        (
            770,
            "TemperatureSensor",
            1026,
            "sensor",
            "°C",
            {"below": 1, "above": 0},
            None,
        ),
        (
            770,
            "TemperatureSensor",
            1026,
            "sensor",
            "°C",
            {"above": "sensor.limit"},
            None,
        ),
        (775, "HumiditySensor", 1029, "sensor", "%", {"below": -1}, None),
    ],
)
async def test_registry_sensor_to_light(
    monkeypatch, device, key, cluster, domain, unit, criteria, expected
):
    def node(index, attributes):
        now = datetime.now(UTC)
        return MatterNode(
            MatterNodeData(index, now, now, 1, True, attributes=attributes)
        )

    source = node(
        21,
        {
            "3/29/0": [{"0": device, "1": 1}],
            f"3/{cluster}/0": False if cluster == 69 else 0,
        },
    )
    lamp = node(
        42, {"1/29/0": [{"0": 256, "1": 1}], "1/6/0": False, "1/6/65529": [0, 1]}
    )
    client = SimpleNamespace(server_info=object(), get_nodes=lambda: [source, lamp])
    runtime = SimpleNamespace(adapter=SimpleNamespace(matter_client=client))

    def entry(domain, uid):
        return SimpleNamespace(
            domain=domain,
            entity_id=f"{domain}.example",
            unique_id=uid,
            labels={"reflex"},
            platform="matter",
            config_entry_id="fabric",
            disabled_by=None,
            capabilities={},
        )

    src = entry(domain, f"ref-3-{key}-{cluster}-0")
    dst = entry("light", "ref-1-MatterLight-6-0")
    auto = entry("automation", "test")
    entries = {e.entity_id: e for e in (src, dst, auto)}
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
            async_get_label_by_name=lambda _: SimpleNamespace(label_id="reflex")
        ),
    )
    config = {
        "triggers": [
            {
                "trigger": "numeric_state" if unit else "state",
                "entity_id": src.entity_id,
                **criteria,
            }
        ],
        "actions": [
            {"action": "light.turn_on", "target": {"entity_id": dst.entity_id}}
        ],
    }
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_get_entry=lambda _: SimpleNamespace(runtime_data=runtime)
        ),
        states=SimpleNamespace(
            get=lambda _: SimpleNamespace(
                state="on", attributes={"unit_of_measurement": unit}
            )
        ),
        data={
            DATA_COMPONENT: SimpleNamespace(
                get_entity=lambda _: SimpleNamespace(raw_config=config)
            )
        },
    )
    result = await async_compile(hass, "fabric")
    if expected is None:
        assert (
            auto.entity_id in result.issues
            and result.image == encode([])
            and not result.permissions
        )
        return
    assert not result.issues
    row = ROW.unpack_from(result.image, 8)
    assert (row[3], EXTENSION.unpack_from(result.image, 40)[0]) == expected
    assert result.permissions == {21: {(3, cluster, 1)}, 42: {(1, 6, 3)}}
    admin = {"1": 5, "2": 2, "3": [1], "4": None, "254": 1}
    _, managed = plan([admin], [], 2, result.permissions[21], 1, 3, 3, True)
    assert managed == [
        {"1": 1, "2": 2, "3": [2], "4": [{"1": 3, "0": cluster, "2": None}], "254": 1}
    ]

    monkeypatch.setattr(source.endpoints[3], "has_attribute", lambda *_: False)
    with pytest.raises(RuntimeError, match="Waiting for the Matter sensor attribute"):
        await async_compile(hass, "fabric")


def test_multiple_thresholds_have_distinct_stable_ids():
    triggers = [
        {"trigger": "numeric_state", "entity_id": "sensor.air", "above": t}
        for t in [1, 2, 1]
    ]
    cfg = {
        "triggers": triggers,
        "actions": [{"action": "light.turn_on", "target": {"entity_id": "light.lamp"}}],
    }
    lookup = {
        "sensor.air": Endpoint(1, 1, sensor_kind="temperature", unit="°C"),
        "light.lamp": Endpoint(2, 1),
    }
    result = compile_automation("levels", cfg, lookup.__getitem__)
    assert len(result) == 2
    assert {row.threshold for row in result} == {100, 200}
    assert encode(result) == encode(list(reversed(result)))
