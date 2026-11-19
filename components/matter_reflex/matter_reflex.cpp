#include "matter_reflex.h"
#include "reflex_http.h"
#include "reflex_peers.h"
#include "reflex_runtime.h"

#include "esphome/core/log.h"
#include <esp_matter_providers.h>
#include <esp_heap_caps.h>
#include <platform/ESP32/ConfigurationManagerImpl.h>
#include <platform/internal/GenericDeviceInstanceInfoProvider.h>
#include <lib/support/SafeString.h>

namespace esphome::matter_reflex {
namespace {
constexpr char TAG[] = "matter_reflex";
ReflexComponent *instance;  // One Reflex endpoint set per Matter node.

// HA verifies BasicInformation.ProductName before it accepts the lease endpoint.
class ReflexDeviceInfo : public chip::DeviceLayer::Internal::GenericDeviceInstanceInfoProvider<
                            chip::DeviceLayer::Internal::ESP32Config> {
 public:
  ReflexDeviceInfo() : GenericDeviceInstanceInfoProvider(chip::DeviceLayer::ConfigurationManagerImpl::GetDefaultInstance()) {}

  CHIP_ERROR GetProductName(char *buffer, size_t size) override {
    constexpr char name[] = "Matter Reflex";
    if (size < sizeof(name)) return CHIP_ERROR_BUFFER_TOO_SMALL;
    chip::Platform::CopyString(buffer, size, name);
    return CHIP_NO_ERROR;
  }
};

bool build_switch(esp_matter::endpoint_t *endpoint) {
  static ReflexDeviceInfo device_info;
  esp_matter::set_custom_device_instance_info_provider(&device_info);
  esp_matter::endpoint::on_off_light_switch::config_t config{};
  if (esp_matter::endpoint::on_off_light_switch::add(endpoint, &config) != ESP_OK)
    return false;
  reflex_runtime::create_cluster(endpoint);
  return true;
}

bool build_legacy_output(esp_matter::endpoint_t *endpoint) {
  esp_matter::endpoint::on_off_plug_in_unit::config_t config{};
  return esp_matter::endpoint::on_off_plug_in_unit::add(endpoint, &config) == ESP_OK;
}
}  // namespace

void ReflexComponent::register_endpoints(matter::MatterComponent *parent) {
  parent_ = parent;
  parent->register_endpoint(1, build_switch);
  parent->register_endpoint(2, build_legacy_output);
}

void ReflexComponent::setup() {
  if (parent_->is_failed()) {
    ESP_LOGE(TAG, "Matter startup failed");
    mark_failed();
    return;
  }
  bool initial_active;
  {
    esp_matter::lock::ScopedChipStackLock lock(portMAX_DELAY);
    reflex_peers::set_subscription_listener([](uint64_t peer) {
      ESP_LOGD(TAG, "Subscribed to peer %016llx, free internal heap %u bytes",
               static_cast<unsigned long long>(peer),
               static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)));
    });
    instance = this;
    reflex_runtime::start([](bool active) {
      ESP_LOGI(TAG, "Lease state: %s", active ? "active" : "standby");
      if (instance->active_sensor_)
        instance->defer([active]() { instance->active_sensor_->publish_state(active); });
    }, [](uint32_t rule, uint64_t event, uint16_t status) {
      // CHIP callbacks run under the stack lock. Run YAML actions on ESPHome's loop.
      instance->defer([rule, event, status]() { instance->result_trigger_.trigger(rule, event, status); });
    });
    initial_active = reflex_runtime::active();
    ESP_LOGI(TAG, "Node %016llx, rules hash %s",
             static_cast<unsigned long long>(reflex_runtime::self_node()), reflex_runtime::rules_hash());
  }
  if (active_sensor_) active_sensor_->publish_initial_state(initial_active);
  if (reflex_http_start(transfer_key_) != ESP_OK) {
    ESP_LOGE(TAG, "Could not start the authenticated rule transfer service");
    mark_failed();
    return;
  }
  ESP_LOGI(TAG, "Reflex runtime started");
}

}  // namespace esphome::matter_reflex
