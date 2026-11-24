"""Limit internal-control cleanup to the configured Reflex node and endpoints."""

from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.matter_reflex.registry import async_setup_entity_cleanup

PREFIX = "0123456789ABCDEF-000000000000001C-MatterNodeDevice-"


@pytest.fixture
def setup_registry(monkeypatch):
    entities = {}
    registry = Mock(async_get=entities.get)
    devices = Mock()
    devices.async_get_device.return_value = SimpleNamespace(id="reflex-device")
    monkeypatch.setattr(er, "async_get", lambda _: registry)
    monkeypatch.setattr(dr, "async_get", lambda _: devices)
    monkeypatch.setattr(
        er, "async_entries_for_config_entry", lambda *_: list(entities.values())
    )
    hass = SimpleNamespace(bus=Mock())
    entry = SimpleNamespace(
        data={"node_id": 28, "matter_entry_id": "matter-entry"},
        async_on_unload=Mock(),
    )
    server = SimpleNamespace(compressed_fabric_id=0x0123456789ABCDEF)

    def register(entity_id, suffix, **overrides):
        values = {
            "entity_id": entity_id,
            "domain": entity_id.split(".")[0],
            "unique_id": PREFIX + suffix,
            "platform": "matter",
            "device_id": "reflex-device",
            "config_entry_id": "matter-entry",
            "disabled_by": None,
        }
        values.update(overrides)
        entities[entity_id] = SimpleNamespace(**values)
        return entities[entity_id]

    return SimpleNamespace(
        hass=hass,
        entry=entry,
        server=server,
        registry=registry,
        devices=devices,
        register=register,
    )


def test_existing_controls_are_disabled_by_identity_not_entity_name(setup_registry):
    ctx = setup_registry
    for entity_id, suffix in (
        ("button.renamed_first", "1-IdentifyButton-3-1"),
        ("button.renamed_second", "2-IdentifyButton-3-1"),
        ("switch.renamed_outlet", "2-MatterPlug-6-0"),
    ):
        ctx.register(entity_id, suffix)

    async_setup_entity_cleanup(ctx.hass, ctx.entry, ctx.server)

    assert ctx.registry.async_update_entity.call_args_list == [
        call(entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION)
        for entity_id in (
            "button.renamed_first",
            "button.renamed_second",
            "switch.renamed_outlet",
        )
    ]
    ctx.devices.async_get_device.assert_called_with(
        identifiers={
            (
                "matter",
                "deviceid_0123456789ABCDEF-000000000000001C-MatterNodeDevice",
            )
        }
    )


@pytest.mark.parametrize(
    ("entity_id", "suffix", "overrides"),
    [
        ("switch.real_outlet", "3-MatterPlug-6-0", {}),
        ("button.real_identify", "3-IdentifyButton-3-1", {}),
        ("button.other_node", "1-IdentifyButton-3-1", {"device_id": "other"}),
        ("button.other_fabric", "1-IdentifyButton-3-1", {"config_entry_id": "other"}),
        ("button.other_platform", "1-IdentifyButton-3-1", {"platform": "esphome"}),
        (
            "button.disabled",
            "1-IdentifyButton-3-1",
            {"disabled_by": er.RegistryEntryDisabler.USER},
        ),
        ("sensor.status", "1-IdentifyButton-3-1", {}),
        ("button.other_command", "1-IdentifyButton-3-2", {}),
        ("select.startup", "2-StartUpOnOff-6-16387", {}),
        ("binary_sensor.active", "active", {"platform": "matter_reflex"}),
    ],
)
def test_unrelated_entities_and_user_disable_flags_are_untouched(
    setup_registry, entity_id, suffix, overrides
):
    ctx = setup_registry
    ctx.register(entity_id, suffix, **overrides)

    async_setup_entity_cleanup(ctx.hass, ctx.entry, ctx.server)

    ctx.registry.async_update_entity.assert_not_called()


def test_late_discovery_and_listener_lifecycle(setup_registry):
    ctx = setup_registry
    async_setup_entity_cleanup(ctx.hass, ctx.entry, ctx.server)
    ctx.hass.bus.async_listen.assert_called_once()
    event_type, listener = ctx.hass.bus.async_listen.call_args.args
    assert event_type == er.EVENT_ENTITY_REGISTRY_UPDATED
    ctx.entry.async_on_unload.assert_called_once_with(
        ctx.hass.bus.async_listen.return_value
    )

    ctx.register("button.late_identify", "1-IdentifyButton-3-1")
    listener(
        SimpleNamespace(data={"action": "create", "entity_id": "button.late_identify"})
    )
    ctx.registry.async_update_entity.assert_called_once_with(
        "button.late_identify", disabled_by=er.RegistryEntryDisabler.INTEGRATION
    )

    # Enabling or renaming an entity is not a discovery event; avoid a loop.
    ctx.registry.async_update_entity.reset_mock()
    listener(
        SimpleNamespace(data={"action": "update", "entity_id": "button.late_identify"})
    )
    listener(
        SimpleNamespace(data={"action": "remove", "entity_id": "button.late_identify"})
    )
    listener(
        SimpleNamespace(data={"action": "create", "entity_id": "button.already_gone"})
    )
    ctx.registry.async_update_entity.assert_not_called()
