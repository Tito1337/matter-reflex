"""Run the Matter Reflex peer and lease engine inside ESPHome."""

import re

from esphome import automation
import esphome.codegen as cg
import esphome.config_validation as cv
import esphome.final_validate as fv
from esphome.components import binary_sensor, sensor
from esphome.components.esp32 import (
    add_idf_sdkconfig_option,
    add_src_cmake,
    include_builtin_idf_component,
)
from esphome.components.matter.types import MatterComponent
from esphome.components.network import require_ipv4
from esphome.const import CONF_ID
from esphome.core import CORE
from esphome.coroutine import CoroPriority, coroutine_with_priority

DOMAIN = "matter_reflex"
AUTO_LOAD = ["binary_sensor", "sensor"]
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
        cv.Optional("api_rules", default=False): cv.boolean,
        cv.Optional("ready_peers"): sensor.sensor_schema(accuracy_decimals=0, entity_category="diagnostic"),
        cv.Optional("required_peers"): sensor.sensor_schema(accuracy_decimals=0, entity_category="diagnostic"),
        cv.Optional("active"): binary_sensor.binary_sensor_schema(),
        cv.Optional("on_rule_result"): automation.validate_automation(single=True),
        cv.Optional("transfer_key"): cv.sensitive(transfer_key),
    }).extend(cv.COMPONENT_SCHEMA),
    # The pinned Matter SDK compiles IPv4 address helpers even on a Thread node.
    require_ipv4(DOMAIN),
)


def final_validate(config):
    full_config = fv.full_config.get()
    if config["api_rules"]:
        api = full_config.get("api") or {}
        if not (api.get("encryption") or {}).get("key") or api["reboot_timeout"].total_milliseconds:
            raise cv.Invalid("API rules require an encryption key and api.reboot_timeout: 0s")
    matter = full_config["matter"]
    if {1, 2}.intersection(matter["endpoints"]):
        raise cv.Invalid("Matter Reflex reserves endpoints 1 and 2")
    if matter.get("vendor_id") != 0xFFF1:
        raise cv.Invalid("Set matter.vendor_id to 0xFFF1 for Home Assistant discovery")


FINAL_VALIDATE_SCHEMA = final_validate


@coroutine_with_priority(CoroPriority.FINAL)
async def enable_reflex_clusters():
    # The Matter component selects clusters after it resolves the user's endpoints.
    # Reflex registers endpoints in C++, so add their clusters after that selection.
    for cluster in ("ACTIONS", "ON_OFF", "GROUPS", "IDENTIFY"):
        add_idf_sdkconfig_option(f"CONFIG_SUPPORT_{cluster}_CLUSTER", True)


async def to_code(config):
    component = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(component, config)
    if config["api_rules"]:
        cg.add_define("USE_REFLEX_API_RULES")
    if "ready_peers" in config:
        ready = await sensor.new_sensor(config["ready_peers"])
        cg.add(component.set_ready_sensor(ready))
    if "required_peers" in config:
        required = await sensor.new_sensor(config["required_peers"])
        cg.add(component.set_required_sensor(required))
    if "active" in config:
        active = await binary_sensor.new_binary_sensor(config["active"])
        cg.add(component.set_active_sensor(active))
    if "on_rule_result" in config:
        await automation.build_automation(
            component.get_result_trigger(),
            [(cg.uint32, "rule_id"), (cg.uint64, "event_number"), (cg.uint16, "status")],
            config["on_rule_result"],
        )
    parent = await cg.get_variable(config["matter_id"])
    cg.add(component.register_endpoints(parent))
    CORE.add_job(enable_reflex_clusters)
    add_idf_sdkconfig_option("CONFIG_CUSTOM_DEVICE_INSTANCE_INFO_PROVIDER", True)
    add_idf_sdkconfig_option("CONFIG_CHIP_TASK_STACK_SIZE", 12288)
    cg.add_cmake_arg("PROJECT_VER_NUMBER", "5")
    add_src_cmake(
        'target_link_options(${COMPONENT_LIB} PUBLIC '
        '"-Wl,--wrap=_ZN4chip18CASESessionManager4InitEPNS_6System5LayerERKNS_24CASESessionManagerConfigE")'
    )
    include_builtin_idf_component("esp_http_server")
    if "transfer_key" in config:
        cg.add(component.set_transfer_key(config["transfer_key"]))
