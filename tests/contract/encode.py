"""Emit a binary MRFX image using the documented automation and real compiler."""

import sys
from pathlib import Path

import yaml

from custom_components.matter_reflex.compiler import Endpoint, compile_automation
from custom_components.matter_reflex.wire import encode

readme = Path("README.md").read_text(encoding="utf-8")
button_example = readme.split("### Button example", 1)[1]
example = yaml.safe_load(button_example.split("```yaml\n", 1)[1].split("```", 1)[0])
endpoints = {
    "event.reading_button": Endpoint(0x123456789ABCDEF0, 2, event_types=("multi_press_2",)),
    "light.reading_lamp": Endpoint(40, 1, dimmable=True),
    "switch.outlet": Endpoint(41, 1, plug_ops=frozenset({0, 1, 2})),
    "cover.shade": Endpoint(42, 1, cover_ops=frozenset({9})),
    "light.dimmer": Endpoint(43, 1, dimmable=True),
}
rows = compile_automation("readme", example, endpoints.__getitem__)
for service, entity, data in (
    ("switch.toggle", "switch.outlet", {}),
    ("cover.set_cover_position", "cover.shade", {"position": 75}),
    ("light.turn_on", "light.dimmer", {"brightness_step": -128}),
):
    config = {
        "triggers": example["triggers"],
        "actions": [{"action": service, "target": {"entity_id": entity}, "data": data}],
    }
    rows.extend(compile_automation(entity, config, endpoints.__getitem__))
sys.stdout.buffer.write(encode(rows))
