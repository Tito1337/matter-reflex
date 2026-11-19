"""Run the Matter Reflex peer and lease engine inside ESPHome."""

import re

import esphome.codegen as cg
import esphome.config_validation as cv
import esphome.final_validate as fv
from esphome.components.esp32 import add_idf_sdkconfig_option, include_builtin_idf_component
from esphome.components.matter.types import MatterComponent
from esphome.components.network import require_ipv4
from esphome.const import CONF_ID

DOMAIN = "matter_reflex"
DEPENDENCIES = ["matter", "openthread"]
ReflexComponent = cg.esphome_ns.namespace(DOMAIN).class_("ReflexComponent", cg.Component)


def transfer_key(value):
    value = cv.string_strict(value)
    if not re.fullmatch("[0-9a-fA-F]{64}", value):
        raise cv.Invalid("transfer_key requires 64 hexadecimal digits")
    return value


CONFIG_SCHEMA = cv.All(
    cv.Schema({
        cv.GenerateID(): cv.declare_id(ReflexComponent),
        cv.GenerateID("matter_id"): cv.use_id(MatterComponent),
        cv.Optional("transfer_key"): cv.sensitive(transfer_key),
    }).extend(cv.COMPONENT_SCHEMA),
    # The pinned Matter SDK compiles IPv4 address helpers even on a Thread node.
    require_ipv4(DOMAIN),
)


def final_validate(config):
    matter = fv.full_config.get()["matter"]
    if {1, 2}.intersection(matter["endpoints"]):
        raise cv.Invalid("Matter Reflex reserves endpoints 1 and 2")
    if matter.get("vendor_id") != 0xFFF1:
        raise cv.Invalid("Set matter.vendor_id to 0xFFF1 for Home Assistant discovery")


FINAL_VALIDATE_SCHEMA = final_validate


async def to_code(config):
    component = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(component, config)
    parent = await cg.get_variable(config["matter_id"])
    cg.add(component.register_endpoints(parent))
    for cluster in ("ACTIONS", "ON_OFF", "GROUPS", "IDENTIFY"):
        add_idf_sdkconfig_option(f"CONFIG_SUPPORT_{cluster}_CLUSTER", True)
    add_idf_sdkconfig_option("CONFIG_CUSTOM_DEVICE_INSTANCE_INFO_PROVIDER", True)
    add_idf_sdkconfig_option("CONFIG_CHIP_TASK_STACK_SIZE", 12288)
    cg.add_cmake_arg("PROJECT_VER_NUMBER", "5")
    include_builtin_idf_component("esp_http_server")
    if "transfer_key" in config:
        cg.add(component.set_transfer_key(config["transfer_key"]))
