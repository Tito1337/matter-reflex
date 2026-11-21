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

#ifdef USE_REFLEX_API_RULES
void ReflexComponent::api_exchange(StringRef fabric, StringRef node, StringRef image, JsonObject reply) {
  // An empty payload reads status. A nonempty payload replaces one complete table.
  // Check both Matter identifiers under the same lock as the commit.
  if (fabric.size() != 16 || node.size() != 16 || image.size() > 2 * reflex::kMaxImage || image.size() % 2) return;
  std::array<uint8_t, reflex::kMaxImage> bytes;
  if (!image.empty() && parse_hex(image.c_str(), image.size(), bytes.data(), image.size() / 2) != image.size()) return;
  esp_matter::lock::ScopedChipStackLock lock(portMAX_DELAY);
  const auto owner = reflex_runtime::self_node();
  char expected_fabric[17], expected_node[17];
  format_hex_to(expected_fabric, reflex_runtime::fabric_id());
  format_hex_to(expected_node, owner);
  if (!owner || fabric != expected_fabric || node != expected_node) return;
  if (!image.empty() && !reflex_runtime::replace_image(bytes.data(), image.size() / 2)) return;
  reply["fabric"] = std::string(expected_fabric);
  reply["node_id"] = std::string(expected_node);
  reply["hash"] = std::string(reflex_runtime::rules_hash());
  reply["active"] = reflex_runtime::active();
  reply["executions"] = reflex_runtime::executions();
}
#endif

void ReflexComponent::publish_readiness() {
  reflex_peers::Readiness counts;
  {
    esp_matter::lock::ScopedChipStackLock lock(portMAX_DELAY);
    counts = reflex_peers::readiness();
  }
  if (ready_sensor_ && ready_sensor_->state != counts.ready) ready_sensor_->publish_state(counts.ready);
  if (required_sensor_ && required_sensor_->state != counts.total) required_sensor_->publish_state(counts.total);
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
      instance->defer([rule, event, status]() {
        instance->result_trigger_.trigger(rule, event, status);
        instance->result_callbacks_.call(rule, event, status);
      });
    });
    initial_active = reflex_runtime::active();
    ESP_LOGI(TAG, "Node %016llx, rules hash %s",
             static_cast<unsigned long long>(reflex_runtime::self_node()), reflex_runtime::rules_hash());
  }
  if (active_sensor_) active_sensor_->publish_initial_state(initial_active);
  if (ready_sensor_ || required_sensor_) {
    publish_readiness();
    set_interval(5000, [this]() { publish_readiness(); });
  }
  if (reflex_http_start(transfer_key_) != ESP_OK) {
    ESP_LOGE(TAG, "Could not start the authenticated rule transfer service");
    mark_failed();
    return;
  }
  ESP_LOGI(TAG, "Reflex runtime started");
}

}  // namespace esphome::matter_reflex
