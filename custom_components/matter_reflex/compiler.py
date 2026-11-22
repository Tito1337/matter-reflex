"""Compile the active HA configuration into the bounded Switch-to-device rule image."""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from hashlib import sha256

from .const import CLUSTER, ENDPOINT, REFLEX_LABEL, VENDOR
from .policy import FALLBACK, configured_policy
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


@dataclass
class ActivityRule:
    entity_id: str
    trigger: tuple[int, int, int, int]
    target: str


@dataclass
class Compilation:
    image: bytes
    issues: dict[str, str]
    permissions: dict[int, set[tuple[int, int, int]]]
    # Rows from one trigger share a source, gesture, and automation entity.
    activity: dict[int, ActivityRule] = field(default_factory=dict)


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


def triggers(raw, lookup):
    for trigger in items(raw):
        only(
            trigger,
            {"trigger", "platform", "target", "options", "alias", "id", "enabled"},
        )
        if (
            trigger.get("enabled", True) is not True
            or trigger.get("trigger", trigger.get("platform")) != "event.received"
        ):
            raise ValueError("Expected an enabled event.received trigger")
        options = trigger.get("options")
        only(options, {"event_type"})
        for source in entities(trigger.get("target"), "event"):
            src = lookup(source)
            for name in items(options.get("event_type")):
                if not isinstance(name, str) or name not in src.event_types:
                    raise ValueError(f"{source} does not expose {name!r}")
                if match := re.fullmatch(r"multi_press_([1-8])", name):
                    yield src, 6, int(match[1])
                elif name in EVENTS:
                    yield src, EVENTS[name], 0
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
        if name == "cover.set_cover_position":
            only(data, {"position"})
            value = data.get("position")
            if type(value) is not int or not 0 <= value <= 100:
                raise ValueError("Cover position must be an integer from 0 to 100")
            return "cover", COVER_OPERATIONS[name], value
        only(data, set())
        return "cover", COVER_OPERATIONS[name], 0
    allowed = {"brightness", "brightness_pct", "brightness_step", "brightness_step_pct"}
    only(data, allowed if name == "light.turn_on" else set())
    if not data:
        return name.partition(".")[0], ON_OFF_OPERATIONS[name], 0
    if len(data) != 1:
        raise ValueError("Choose one brightness setting")
    key, value = next(iter(data.items()))
    maximum = 100 if key.endswith("_pct") else 255
    minimum = -maximum if "step" in key else 0
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{key} must be an integer from {minimum} to {maximum}")
    if key == "brightness_step_pct":
        return "light", 5, value
    return (
        "light",
        (4 if "step" in key else 3),
        (round(value * 255 / 100) if maximum == 100 else value),
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
        domain, op, value = action_values(action)
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
            if op in (3, 4, 5) and not dst.dimmable:
                raise ValueError(f"{name} has no Level Control server")
            for src, event, presses in sources:
                identity_bytes = json.dumps(
                    [identity, src.node, src.ep, event, presses, dst.node, dst.ep],
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
    hass,
    matter_entry_id: str,
    reflex_node: int | None = None,
    entry_id: str | None = None,
) -> Compilation:
    from homeassistant.components.automation.const import DATA_COMPONENT
    from homeassistant.components.matter.helpers import get_device_id
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers import label_registry as lr
    from matter_server.client.models.device_types import (
        DimmablePlugInUnit,
        OnOffPlugInUnit,
    )

    registry = er.async_get(hass)
    label = lr.async_get(hass).async_get_label_by_name(REFLEX_LABEL)
    if label is None:
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

    dimmable = set()

    def accepted_commands(endpoint, cluster, entity_id):
        attribute = 0xFFF9  # AcceptedCommandList
        # The CHIP class exposes an empty default before this path is read.
        if endpoint.has_attribute(cluster, attribute):
            commands = endpoint.get_attribute_value(cluster, attribute)
            if isinstance(commands, list):
                return commands
        raise RuntimeError(f"Waiting for the Matter commands of {entity_id}")

    target_names = {}

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
        if domain != "event":
            target_names[(node.node_id, ep.endpoint_id)] = entity_id
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
                or not ep.device_types.intersection(
                    {OnOffPlugInUnit, DimmablePlugInUnit}
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
            commands = accepted_commands(ep, 6, entity_id)
            # A toggle is resolved to absolute On or Off by the firmware.
            plug_ops = {op for op in (0, 1) if op in commands}
            if plug_ops == {0, 1}:
                plug_ops.add(2)
            return Endpoint(node.node_id, ep.endpoint_id, plug_ops=frozenset(plug_ops))
        state = hass.states.get(entity_id)
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
        level = ep.get_cluster(8) if domain == "light" else None
        if level is not None:
            dimmable.add((node.node_id, ep.endpoint_id))
        lo = (level.minLevel or 1) if level else 1
        hi = (level.maxLevel or 254) if level else 254
        if type(lo) is not int or type(hi) is not int or not 1 <= lo < hi <= 254:
            lo, hi = 1, 254
        events = (reg.capabilities or {}).get("event_types") or (
            state.attributes.get("event_types", ()) if state else ()
        )
        if domain == "event" and not events:
            raise RuntimeError(f"Waiting for event capabilities of {entity_id}")
        return Endpoint(
            node.node_id, ep.endpoint_id, lo, hi, level is not None, tuple(events)
        )

    component = hass.data.get(DATA_COMPONENT)
    if component is None:
        raise RuntimeError("HA automations have not finished loading")
    rules, issues, permissions, activity = [], {}, {}, {}
    for reg in sorted(registry.entities.values(), key=lambda ent: ent.entity_id):
        if (
            reg.domain != "automation"
            or reg.disabled_by
            or label.label_id not in reg.labels
        ):
            continue
        entity_id = reg.entity_id
        state = hass.states.get(entity_id)
        if state and state.state == "off":
            continue
        automation = component.get_entity(entity_id)
        if not automation or not state or state.state != "on":
            raise RuntimeError(f"Waiting for automation {entity_id}")
        config = automation.raw_config
        if not config:
            issues[entity_id] = "Automation configuration cannot be read"
            continue
        try:
            explicit = configured_policy(config)
            handlers = FALLBACK
            if explicit is not None:
                selected, handlers, remaining = explicit
                if selected != entry_id or "esp" not in (
                    handlers.normal,
                    handlers.degraded,
                ):
                    continue
                config = {**config, "conditions": remaining}
            dimmable.clear()
            target_names.clear()
            added = compile_automation(reg.unique_id, config, lookup)
            if handlers.normal == "esp":
                added = [replace(row, direct=True) for row in added]
            encode(
                rules + added
            )  # capacity and hash collision checked atomically per automation
        except (ValueError, KeyError, TypeError) as exc:
            issues[entity_id] = str(exc)
            continue
        rules.extend(added)
        for row in added:
            if row.direct:
                activity[row.rule_id] = ActivityRule(
                    entity_id,
                    (row.source, row.source_ep, row.event, row.presses),
                    target_names[(row.target, row.target_ep)],
                )
            permissions.setdefault(row.source, set()).add((row.source_ep, 59, 1))
            dest = permissions.setdefault(row.target, set())
            if row.op in COVER_OPERATIONS.values():
                dest.add((row.target_ep, WINDOW_COVERING, 3))
            else:
                dest.add((row.target_ep, 6, 3))
                if (row.target, row.target_ep) in dimmable:
                    dest.add((row.target_ep, 8, 3))
    return Compilation(encode(rules), issues, permissions, activity)
