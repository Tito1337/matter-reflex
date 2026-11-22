"""Compile the automations exercised on the offline Matter network."""

import sys
from dataclasses import replace
from pathlib import Path

import yaml

from custom_components.matter_reflex.compiler import Endpoint, compile_automation
from custom_components.matter_reflex.wire import encode

lamp = Endpoint(42, 1, dimmable=True, supports_transition=True)
plug = Endpoint(42, 2, plug_ops=frozenset({1}))
shade = Endpoint(42, 3, cover_ops=frozenset({10}))
rows = []
readme = Path("README.md").read_text()
for ep, model, kind, source in (
    (1, "MYGGBETT", "contact", "binary_sensor.front_door"),
    (2, "MYGGSPRAY", "occupancy", "binary_sensor.hall_motion"),
):
    section = readme.split(f"### IKEA {model}", 1)[1]
    config = yaml.safe_load(section.split("```yaml\n", 1)[1].split("```", 1)[0])
    endpoints = {source: Endpoint(21, ep, sensor_kind=kind), "light.hall": lamp}
    rows.extend(compile_automation(kind, config, endpoints.__getitem__))

for ep, kind, unit, bound, action, target, dest, data in (
    (3, "leak", "", {"to": "on"}, "switch.turn_on", "switch.outlet", plug, {}),
    (
        4,
        "temperature",
        "°F",
        {"below": 32},
        "cover.set_cover_tilt_position",
        "cover.shade",
        shade,
        {"tilt_position": 25},
    ),
    (5, "humidity", "%", {"above": 55}, "light.turn_off", "light.hall", lamp, {}),
):
    source = f"{'sensor' if unit else 'binary_sensor'}.{kind}"
    endpoints = {source: Endpoint(21, ep, sensor_kind=kind, unit=unit), target: dest}
    config = {
        "triggers": [
            {
                "trigger": "numeric_state" if unit else "state",
                "entity_id": source,
                **bound,
            }
        ],
        "actions": [{"action": action, "target": {"entity_id": target}, "data": data}],
    }
    compiled = compile_automation(kind, config, endpoints.__getitem__)
    if kind == "leak":
        compiled = [replace(row, direct=True) for row in compiled]
    rows.extend(compiled)
sys.stdout.buffer.write(encode(rows))
