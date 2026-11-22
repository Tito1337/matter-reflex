"""Compile the active HA configuration into the bounded Matter rule image."""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256
from math import ceil, floor

from .const import CLUSTER, ENDPOINT, VENDOR
from .wire import Rule, encode

EVENTS = {
    "switch_latched": 0,
    "initial_press": 1,
    "long_press": 2,
    "short_release": 3,
    "long_release": 4,
}
ON_OFF_OPERATIONS = {
    "light.turn_off": 0,
    "light.turn_on": 1,
    "light.toggle": 2,
    "switch.turn_off": 0,
    "switch.turn_on": 1,
    "switch.toggle": 2,
}
COVER_OPERATIONS = {
    "cover.open_cover": 6,
    "cover.close_cover": 7,
    "cover.stop_cover": 8,
    "cover.set_cover_position": 9,
    "cover.set_cover_tilt_position": 10,
    "cover.open_cover_tilt": 11,
    "cover.close_cover_tilt": 12,
    "cover.stop_cover_tilt": 13,
}
# Attribute trigger codes follow the standard Switch event IDs.
BOOLEAN_FALSE, BOOLEAN_TRUE, UNOCCUPIED, OCCUPIED = range(7, 11)
TEMP_ABOVE, TEMP_BELOW, HUMIDITY_ABOVE, HUMIDITY_BELOW = range(11, 15)
SOURCE_CLUSTERS = {
    BOOLEAN_FALSE: 0x45,
    BOOLEAN_TRUE: 0x45,
    UNOCCUPIED: 0x406,
    OCCUPIED: 0x406,
    TEMP_ABOVE: 0x402,
    TEMP_BELOW: 0x402,
    HUMIDITY_ABOVE: 0x405,
    HUMIDITY_BELOW: 0x405,
}
WINDOW_COVERING = 0x0102


@dataclass(frozen=True)
class Endpoint:
    node: int
    ep: int
    minimum: int = 1
    maximum: int = 254
    dimmable: bool = False
    event_types: tuple[str, ...] = ()
    cover_ops: frozenset[int] = frozenset()
    plug_ops: frozenset[int] = frozenset()
    sensor_kind: str = ""
    unit: str = ""
    supports_transition: bool = False
    light_power_ops: frozenset[int] = frozenset({0, 1, 2})


@dataclass
class Compilation:
    image: bytes
    issues: dict[str, str]
    permissions: dict[int, set[tuple[int, int, int]]]


def only(obj, allowed):
    if not isinstance(obj, dict) or (extra := set(obj) - set(allowed)):
        raise ValueError(
            f"Unsupported fields: {sorted(extra) if isinstance(obj, dict) else obj!r}"
        )


def items(value):
    values = value if isinstance(value, list) else [value]
    if not values:
        raise ValueError("An explicit non-empty selection is required")
    return values


def entities(target, domain):
    only(target, {"entity_id"})
    values = items(target.get("entity_id"))
    if not all(
        isinstance(x, str) and re.fullmatch(domain + r"\.[a-z0-9_]+", x) for x in values
    ):
        raise ValueError(f"Explicit {domain} entity IDs are required")
    return sorted(set(values))


def numeric_threshold(src, bound, raw):
    if type(raw) not in (int, float, str):
        raise ValueError("Expected a fixed numeric threshold")
    try:
        threshold = Fraction(str(raw))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError("Expected a finite numeric threshold") from exc
    if src.sensor_kind == "temperature":
        if src.unit == "°F":
            threshold = (threshold - 32) * 5 / 9
        elif src.unit == "K":
            threshold -= Fraction("273.15")
        elif src.unit != "°C":
            raise ValueError(f"Unsupported temperature unit {src.unit!r}")
        low, high = -27315, 32767
        above, below = TEMP_ABOVE, TEMP_BELOW
    elif src.sensor_kind == "humidity" and src.unit == "%":
        low, high = 0, 10000
        above, below = HUMIDITY_ABOVE, HUMIDITY_BELOW
    else:
        raise ValueError(
            "Expected a Matter temperature or relative humidity measurement"
        )
    scaled = threshold * 100
    if not low <= scaled <= high:
        raise ValueError("Threshold outside the Matter measurement range")
    return (above, floor(scaled)) if bound == "above" else (below, ceil(scaled))


def triggers(raw, lookup):
    for trigger in items(raw):
        if not isinstance(trigger, dict) or trigger.get("enabled", True) is not True:
            raise ValueError("Expected an enabled trigger")
        kind = trigger.get("trigger", trigger.get("platform"))
        common = {"trigger", "platform", "entity_id", "alias", "id", "enabled"}
        if kind == "state":
            only(trigger, common | {"from", "to"})
            to = trigger.get("to")
            if to not in ("on", "off") or (
                "from" in trigger and trigger["from"] != ("off" if to == "on" else "on")
            ):
                raise ValueError(
                    "Use an on/off destination and optionally the opposite from state"
                )
            for source in entities(
                {"entity_id": trigger.get("entity_id")}, "binary_sensor"
            ):
                src = lookup(source)
                if src.sensor_kind == "contact":
                    event = BOOLEAN_FALSE if to == "on" else BOOLEAN_TRUE
                elif src.sensor_kind == "leak":
                    event = BOOLEAN_TRUE if to == "on" else BOOLEAN_FALSE
                elif src.sensor_kind == "occupancy":
                    event = OCCUPIED if to == "on" else UNOCCUPIED
                else:
                    raise ValueError(f"Unsupported Matter binary sensor {source}")
                yield src, event, 0, 0
            continue
        if kind == "numeric_state":
            only(trigger, common | {"above", "below"})
            bounds = trigger.keys() & {"above", "below"}
            if len(bounds) != 1:
                raise ValueError("Choose one fixed above or below threshold")
            bound = bounds.pop()
            for source in entities({"entity_id": trigger.get("entity_id")}, "sensor"):
                src = lookup(source)
                event, threshold = numeric_threshold(src, bound, trigger[bound])
                yield src, event, 0, threshold
            continue
        only(
            trigger,
            {"trigger", "platform", "target", "options", "alias", "id", "enabled"},
        )
        if kind != "event.received":
            raise ValueError("Expected an enabled event.received trigger")
        options = trigger.get("options")
        only(options, {"event_type"})
        for source in entities(trigger.get("target"), "event"):
            src = lookup(source)
            for name in items(options.get("event_type")):
                if not isinstance(name, str) or name not in src.event_types:
                    raise ValueError(f"{source} does not expose {name!r}")
                if match := re.fullmatch(r"multi_press_([1-8])", name):
                    yield src, 6, int(match[1]), 0
                elif name in EVENTS:
                    yield src, EVENTS[name], 0, 0
                else:
                    raise ValueError(f"Unsupported Switch event: {name}")


def action_values(action):
    only(
        action, {"action", "service", "target", "data", "metadata", "alias", "enabled"}
    )
    name = action.get("action", action.get("service"))
    if (
        not isinstance(name, str)
        or (name not in ON_OFF_OPERATIONS and name not in COVER_OPERATIONS)
        or action.get("enabled", True) is not True
    ):
        raise ValueError("Expected a supported, enabled light, plug or cover action")
    data = action.get("data", {})
    if name in COVER_OPERATIONS:
        if name in ("cover.set_cover_position", "cover.set_cover_tilt_position"):
            field = "tilt_position" if "tilt" in name else "position"
            only(data, {field})
            value = data.get(field)
            if type(value) is not int or not 0 <= value <= 100:
                raise ValueError("Cover position must be an integer from 0 to 100")
            return "cover", COVER_OPERATIONS[name], value, 0
        only(data, set())
        return "cover", COVER_OPERATIONS[name], 0, 0
    allowed = {"brightness", "brightness_pct", "brightness_step", "brightness_step_pct"}
    transition = 0
    if name == "light.turn_on" and isinstance(data, dict) and "transition" in data:
        data = dict(data)
        seconds = data.pop("transition")
        if type(seconds) not in (float, int) or not 0 <= seconds <= 6553.4 or not data:
            raise ValueError(
                "A brightness transition needs a level or step and 0–6553.4 seconds"
            )
        transition = int(Fraction(str(seconds)) * 10)
    only(data, allowed if name == "light.turn_on" else set())
    if not data:
        return name.partition(".")[0], ON_OFF_OPERATIONS[name], 0, 0
    if len(data) != 1:
        raise ValueError("Choose one brightness setting")
    key, value = next(iter(data.items()))
    maximum = 100 if key.endswith("_pct") else 255
    minimum = -maximum if "step" in key else 0
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{key} must be an integer from {minimum} to {maximum}")
    if key == "brightness_step_pct":
        return "light", 5, value, transition
    return (
        "light",
        (4 if "step" in key else 3),
        (round(value * 255 / 100) if maximum == 100 else value),
        transition,
    )


def compile_automation(
    identity: str, cfg: dict, lookup: Callable[[str], Endpoint]
) -> list[Rule]:
    only(
        cfg,
        {
            "id",
            "alias",
            "description",
            "triggers",
            "trigger",
            "conditions",
            "condition",
            "actions",
            "action",
            "mode",
            "initial_state",
            "trace",
            "max_exceeded",
        },
    )
    if cfg.get("conditions", cfg.get("condition", [])) or cfg.get(
        "mode", "single"
    ) not in {"single", "restart", "parallel", "queued"}:
        raise ValueError("Conditions and nonstandard execution modes are outside v1")
    actions = items(cfg.get("actions", cfg.get("action")))
    sources = set(triggers(cfg.get("triggers", cfg.get("trigger")), lookup))
    rules = []
    destinations = set()
    for action in actions:
        domain, op, value, transition = action_values(action)
        for name in entities(action.get("target"), domain):
            dst = lookup(name)
            destination = (dst.node, dst.ep)
            if destination in destinations:
                raise ValueError("Sequential actions on the same endpoint require HA")
            destinations.add(destination)
            if domain == "switch" and op not in dst.plug_ops:
                raise ValueError(f"{name} does not support the requested plug action")
            if domain == "cover" and op not in dst.cover_ops:
                raise ValueError(f"{name} does not support the requested cover action")
            if domain == "light" and op <= 2 and op not in dst.light_power_ops:
                raise ValueError(
                    f"{name} does not support the requested light power action"
                )
            if op in (3, 4, 5) and not dst.dimmable:
                raise ValueError(f"{name} does not support brightness control")
            if transition and not dst.supports_transition:
                raise ValueError(f"{name} does not support brightness transitions")
            for src, event, presses, threshold in sources:
                identity_bytes = json.dumps(
                    [identity, src.node, src.ep, event, presses, dst.node, dst.ep]
                    + ([threshold] if event >= TEMP_ABOVE else []),
                    separators=(",", ":"),
                ).encode()
                rule_id = (
                    int.from_bytes(sha256(identity_bytes).digest()[:4], "big") or 1
                )
                rules.append(
                    Rule(
                        rule_id,
                        src.node,
                        src.ep,
                        event,
                        presses,
                        dst.node,
                        dst.ep,
                        op,
                        value,
                        dst.minimum,
                        dst.maximum,
                        threshold,
                        transition,
                    )
                )
    return rules


def cover_operations(features, accepted_commands):
    """Require both the HA entity service feature and the endpoint's Matter command."""
    from chip.clusters import Objects as clusters
    from homeassistant.components.cover import CoverEntityFeature

    commands = clusters.WindowCovering.Commands
    capabilities = (
        ("cover.open_cover", CoverEntityFeature.OPEN, commands.UpOrOpen),
        ("cover.close_cover", CoverEntityFeature.CLOSE, commands.DownOrClose),
        ("cover.stop_cover", CoverEntityFeature.STOP, commands.StopMotion),
        ("cover.open_cover_tilt", CoverEntityFeature.OPEN_TILT, commands.UpOrOpen),
        ("cover.close_cover_tilt", CoverEntityFeature.CLOSE_TILT, commands.DownOrClose),
        ("cover.stop_cover_tilt", CoverEntityFeature.STOP_TILT, commands.StopMotion),
        (
            "cover.set_cover_tilt_position",
            CoverEntityFeature.SET_TILT_POSITION,
            commands.GoToTiltPercentage,
        ),
        (
            "cover.set_cover_position",
            CoverEntityFeature.SET_POSITION,
            commands.GoToLiftPercentage,
        ),
    )
    return frozenset(
        COVER_OPERATIONS[name]
        for name, feature, command in capabilities
        if features & feature and command.command_id in accepted_commands
    )


async def async_compile(
    hass, matter_entry_id: str, reflex_node: int | None = None
) -> Compilation:
    from homeassistant.components.automation.const import DATA_COMPONENT
    from homeassistant.components.light import LightEntityFeature
    from homeassistant.components.matter.helpers import get_device_id
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers import label_registry as lr
    from matter_server.client.models.device_types import (
        ContactSensor,
        DimmablePlugInUnit,
        OnOffPlugInUnit,
        WaterLeakDetector,
    )

    registry = er.async_get(hass)
    labels = lr.async_get(hass)
    label = labels.async_get_label_by_name("reflex")
    if not label:
        return Compilation(encode([]), {}, {})
    entry = hass.config_entries.async_get_entry(matter_entry_id)
    if not entry or not entry.runtime_data:
        raise RuntimeError("Matter integration is unavailable")
    client = entry.runtime_data.adapter.matter_client
    if client.server_info is None:
        raise RuntimeError("Matter server information is unavailable")
    endpoints = {
        f"{get_device_id(client.server_info, ep)}-{ep.endpoint_id}": (node, ep)
        for node in client.get_nodes()
        for ep in node.endpoints.values()
    }

    def accepted_commands(endpoint, cluster, entity_id):
        attribute = 0xFFF9  # AcceptedCommandList
        # The CHIP class exposes an empty default before this path is read.
        if endpoint.has_attribute(cluster, attribute):
            commands = endpoint.get_attribute_value(cluster, attribute)
            if isinstance(commands, list):
                return commands
        raise RuntimeError(f"Waiting for the Matter commands of {entity_id}")

    def power_operations(endpoint, entity_id):
        commands = accepted_commands(endpoint, 6, entity_id)
        ops = {op for op in (0, 1) if op in commands}
        if ops == {0, 1}:
            ops.add(2)  # Toggle resolves to an absolute command using cached state.
        return frozenset(ops)

    def lookup(entity_id):
        reg = registry.async_get(entity_id)
        if (
            not reg
            or reg.disabled_by
            or reg.config_entry_id != matter_entry_id
            or reg.platform != "matter"
        ):
            raise ValueError(
                f"{entity_id} is not an enabled entity on this Matter fabric"
            )
        parts = reg.unique_id.rsplit("-", 3)
        if len(parts) != 4:
            raise ValueError(
                f"Could not resolve the current Matter endpoint for {entity_id}"
            )
        if parts[0] not in endpoints:
            raise RuntimeError(f"Waiting for Matter endpoint {entity_id}")
        node, ep = endpoints[parts[0]]
        domain = reg.domain
        state = hass.states.get(entity_id)
        if domain in ("binary_sensor", "sensor"):
            cluster = int(parts[2])
            if parts[3] != "0":
                raise ValueError(f"Unsupported Matter sensor attribute on {entity_id}")
            if domain == "binary_sensor" and cluster == 0x45:
                if ContactSensor in ep.device_types and parts[1] == "ContactSensor":
                    kind = "contact"
                elif (
                    WaterLeakDetector in ep.device_types
                    and parts[1] == "WaterLeakDetector"
                ):
                    kind = "leak"
                else:
                    raise ValueError(
                        f"Unsupported Boolean State device type on {entity_id}"
                    )
            elif domain == "binary_sensor" and cluster == 0x406:
                kind = "occupancy"
            elif domain == "sensor" and cluster in (0x402, 0x405):
                kind = "temperature" if cluster == 0x402 else "humidity"
                if not state or not state.attributes.get("unit_of_measurement"):
                    raise RuntimeError(f"Waiting for measurement units of {entity_id}")
            else:
                raise ValueError(f"Unsupported Matter sensor cluster on {entity_id}")
            # An incomplete sensor interview must not replace an installed rule
            # image with one that omits this automation.
            if not ep.has_attribute(cluster, 0):
                raise RuntimeError(
                    f"Waiting for the Matter sensor attribute of {entity_id}"
                )
            return Endpoint(
                node.node_id,
                ep.endpoint_id,
                sensor_kind=kind,
                unit=state.attributes.get("unit_of_measurement", "") if state else "",
            )
        required_cluster = {
            "light": 6,
            "event": 59,
            "cover": WINDOW_COVERING,
            "switch": 6,
        }[domain]
        if ep.get_cluster(required_cluster) is None:
            raise ValueError(f"Missing required Matter cluster on {entity_id}")
        if domain == "switch":
            # The same endpoint can expose configuration switches for child locks
            # or vendor settings; only the primary OnOff power control is eligible.
            if (
                reg.entity_category is not None
                or parts[1] not in ("MatterPlug", "MatterSwitch")
                or parts[2:] != ["6", "0"]
                or (
                    not ep.device_types.intersection(
                        {OnOffPlugInUnit, DimmablePlugInUnit}
                    )
                    and getattr(reg, "original_device_class", None) != "outlet"
                )
            ):
                raise ValueError(f"{entity_id} is not a Matter plug OnOff power switch")
            attrs = node.node_data.attributes
            # Compare the configured fabric-local node even with an incomplete
            # interview. Other Reflex boards also expose this distinctive cluster.
            if (
                node.node_id == reflex_node
                or (
                    attrs.get("0/40/2") == VENDOR
                    and attrs.get("0/40/3") == "Matter Reflex"
                )
                or CLUSTER in (attrs.get(f"{ENDPOINT}/29/1") or [])
            ):
                raise ValueError("The Reflex lease endpoint cannot be a rule target")
            return Endpoint(
                node.node_id, ep.endpoint_id, plug_ops=power_operations(ep, entity_id)
            )
        if domain == "cover":
            commands = accepted_commands(ep, WINDOW_COVERING, entity_id)
            features = (
                state.attributes.get("supported_features", reg.supported_features)
                if state
                else reg.supported_features
            )
            if features is None:
                raise RuntimeError(f"Waiting for cover capabilities of {entity_id}")
            return Endpoint(
                node.node_id,
                ep.endpoint_id,
                cover_ops=cover_operations(features, commands),
            )
        if domain == "event":
            events = (reg.capabilities or {}).get("event_types") or (
                state.attributes.get("event_types", ()) if state else ()
            )
            if not events:
                raise RuntimeError(f"Waiting for event capabilities of {entity_id}")
            return Endpoint(node.node_id, ep.endpoint_id, event_types=tuple(events))
        power_ops = power_operations(ep, entity_id)
        level = ep.get_cluster(8)
        modes = (state.attributes.get("supported_color_modes") if state else None) or (
            reg.capabilities or {}
        ).get("supported_color_modes")
        can_dim = bool(
            level
            and ep.has_attribute(8, 0)
            and (not modes or set(modes) != {"onoff"})
            and 4 in accepted_commands(ep, 8, entity_id)
            and 0 in power_ops
        )

        lo = (level.minLevel or 1) if level else 1
        hi = (level.maxLevel or 254) if level else 254
        if type(lo) is not int or type(hi) is not int or not 1 <= lo < hi <= 254:
            lo, hi = 1, 254
        return Endpoint(
            node.node_id,
            ep.endpoint_id,
            lo,
            hi,
            can_dim,
            light_power_ops=power_ops,
            supports_transition=bool(
                state
                and state.attributes.get("supported_features", 0)
                & LightEntityFeature.TRANSITION
            ),
        )

    component = hass.data.get(DATA_COMPONENT)
    if component is None:
        raise RuntimeError("HA automations have not finished loading")
    rules, issues, permissions = [], {}, {}
    for reg in sorted(registry.entities.values(), key=lambda ent: ent.entity_id):
        if (
            reg.domain != "automation"
            or label.label_id not in reg.labels
            or reg.disabled_by
        ):
            continue
        entity_id = reg.entity_id
        state = hass.states.get(entity_id)
        if state and state.state == "off":
            continue
        automation = component.get_entity(entity_id)
        if not automation or not state or state.state not in ("on", "off"):
            raise RuntimeError(f"Waiting for automation {entity_id}")
        if not automation.raw_config:
            issues[entity_id] = (
                "Automation is unavailable or its configuration cannot be read"
            )
            continue
        try:
            added = compile_automation(reg.unique_id, automation.raw_config, lookup)
            encode(
                rules + added
            )  # capacity and hash collision checked atomically per automation
        except (ValueError, KeyError, TypeError) as exc:
            issues[entity_id] = str(exc)
            continue
        rules.extend(added)
        for row in added:
            permissions.setdefault(row.source, set()).add(
                (row.source_ep, SOURCE_CLUSTERS.get(row.event, 59), 1)
            )
            dest = permissions.setdefault(row.target, set())
            if row.op in COVER_OPERATIONS.values():
                dest.add((row.target_ep, WINDOW_COVERING, 3))
            else:
                dest.add((row.target_ep, 6, 3))
                if row.op in (3, 4, 5):
                    dest.add((row.target_ep, 8, 3))
    return Compilation(encode(rules), issues, permissions)
