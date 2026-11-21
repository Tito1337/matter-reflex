"""Publish the last command result through a normal ESPHome text sensor."""

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import text_sensor
from esphome.components.matter_reflex import ReflexComponent

DEPENDENCIES = ["matter_reflex"]
LastResult = cg.esphome_ns.namespace("reflex_last_result").class_(
    "LastResult", text_sensor.TextSensor
)
CONFIG_SCHEMA = text_sensor.text_sensor_schema(LastResult).extend(
    {cv.GenerateID("reflex_id"): cv.use_id(ReflexComponent)}
)


async def to_code(config):
    sensor = await text_sensor.new_text_sensor(config)
    parent = await cg.get_variable(config["reflex_id"])
    cg.add(sensor.subscribe(parent))
