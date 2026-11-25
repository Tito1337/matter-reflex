"""Run the Matter Reflex peer and lease engine inside ESPHome."""

from esphome import automation
import esphome.codegen as cg
import esphome.config_validation as cv
import esphome.final_validate as fv
from esphome.components import binary_sensor, sensor
from esphome.components.esp32 import (
    add_idf_sdkconfig_option,
    add_src_cmake,
)
from esphome.components.matter.types import MatterComponent
from esphome.components.network import require_ipv4
from esphome.const import CONF_ID
from esphome.core import CORE
from esphome.coroutine import CoroPriority, coroutine_with_priority

DOMAIN = "matter_reflex"
AUTO_LOAD = ["binary_sensor", "sensor"]
DEPENDENCIES = ["matter"]
ReflexComponent = cg.esphome_ns.namespace(DOMAIN).class_("ReflexComponent", cg.Component)


CONFIG_SCHEMA = cv.All(
    cv.Schema({
        cv.GenerateID(): cv.declare_id(ReflexComponent),
        cv.GenerateID("matter_id"): cv.use_id(MatterComponent),
        cv.Optional("api_rules", default=False): cv.boolean,
        cv.Optional("ready_peers"): sensor.sensor_schema(accuracy_decimals=0, entity_category="diagnostic"),
        cv.Optional("required_peers"): sensor.sensor_schema(accuracy_decimals=0, entity_category="diagnostic"),
        cv.Optional("active"): binary_sensor.binary_sensor_schema(),
        cv.Optional("on_rule_result"): automation.validate_automation(single=True),
    }).extend(cv.COMPONENT_SCHEMA),
    # The pinned Matter SDK compiles IPv4 address helpers even on a Thread node.
    require_ipv4(DOMAIN),
)


def final_validate(config):
    full_config = fv.full_config.get()
    if "openthread" not in full_config and not (full_config.get("wifi") or {}).get("networks"):
        raise cv.Invalid("Matter Reflex requires OpenThread or a Wi-Fi station network")
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
async def finalize_matter_config():
    # The Matter component selects clusters after it resolves the user's endpoints.
    # Reflex registers endpoints in C++, so add their clusters after that selection.
    if "wifi" in CORE.loaded_integrations and "openthread" in CORE.loaded_integrations:
        # ESP-IDF's border-router netif requires 12 IPv6 address slots.
        add_idf_sdkconfig_option("CONFIG_LWIP_IPV6_NUM_ADDRESSES", 12)
        # Both IDF commissioning drivers default to root endpoint 0. The Thread
        # dataset is already supplied to ESPHome's router. Only the Wi-Fi driver
        # should register a Network Commissioning server on the root endpoint.
        add_idf_sdkconfig_option("CONFIG_THREAD_NETWORK_COMMISSIONING_DRIVER", False)
        # The mDNS service budget is shared with all delegated SRP records.
        add_idf_sdkconfig_option("CONFIG_MDNS_MAX_SERVICES", 96)
    if "wifi" in CORE.loaded_integrations:
        # ESPHome's border router proxies SRP services into Espressif mDNS.
        # Use the Wi-Fi DNS-SD backend for LAN and delegated Thread records.
        add_src_cmake(
            'set_source_files_properties(esphome/components/matter/matter_dnssd.cpp PROPERTIES '
            'SKIP_PRECOMPILE_HEADERS ON COMPILE_DEFINITIONS REFLEX_WIFI_DNSSD COMPILE_OPTIONS '
            '"-include;${CMAKE_CURRENT_SOURCE_DIR}/esphome/components/matter_reflex/wifi_dnssd.h")'
        )
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
    CORE.add_job(finalize_matter_config)
    add_idf_sdkconfig_option("CONFIG_CUSTOM_DEVICE_INSTANCE_INFO_PROVIDER", True)
    add_idf_sdkconfig_option("CONFIG_CHIP_TASK_STACK_SIZE", 12288)
    cg.add_cmake_arg("PROJECT_VER_NUMBER", "6")
    add_src_cmake(
        'target_link_options(${COMPONENT_LIB} PUBLIC '
        '"-Wl,--wrap=_ZN4chip18CASESessionManager4InitEPNS_6System5LayerERKNS_24CASESessionManagerConfigE")'
    )
