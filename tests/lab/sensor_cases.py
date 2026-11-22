"""HA automations and the wire command and actuator state required for each input."""

from typing import NamedTuple


class SensorCase(NamedTuple):
    sensor: str
    bounds: dict
    service: str
    target: str
    data: dict
    receipt: dict
    state_change: dict


SENSOR_CASES = (
    SensorCase(
        "contact",
        {"to": "on"},
        "switch.turn_on",
        "outlet",
        {},
        {"command": "plugOn"},
        {"plug": True},
    ),
    SensorCase(
        "occupancy",
        {"to": "on"},
        "light.turn_on",
        "dimmer",
        {"brightness": 200, "transition": 0.2},
        {"command": "level", "level": 199, "transition": 2},
        {"on": True, "level": 199},
    ),
    SensorCase(
        "leak",
        {"to": "on"},
        "switch.turn_off",
        "outlet",
        {},
        {"command": "plugOff"},
        {"plug": False},
    ),
    SensorCase(
        "temperature",
        {"below": 0},
        "cover.set_cover_position",
        "lift",
        {"position": 25},
        {"command": "lift", "value": 7500},
        {"lift": 7500},
    ),
    SensorCase(
        "humidity",
        {"above": 55},
        "cover.set_cover_position",
        "lift",
        {"position": 80},
        {"command": "lift", "value": 2000},
        {"lift": 2000},
    ),
)
