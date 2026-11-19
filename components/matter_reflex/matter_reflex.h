#pragma once

#include "esphome/core/automation.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/core/component.h"
#include "esphome/components/binary_sensor/binary_sensor.h"
#include "esphome/components/matter/matter_component.h"

#ifdef USE_REFLEX_API_RULES
#include "esphome/components/json/json_util.h"
#include "esphome/core/string_ref.h"
#endif

namespace esphome::matter_reflex {

class ReflexComponent : public Component {
 public:
  void register_endpoints(matter::MatterComponent *parent);
  void set_transfer_key(const char *key) { transfer_key_ = key; }
  void set_ready_sensor(sensor::Sensor *sensor) { ready_sensor_ = sensor; }
  void set_required_sensor(sensor::Sensor *sensor) { required_sensor_ = sensor; }
  void set_active_sensor(binary_sensor::BinarySensor *sensor) { active_sensor_ = sensor; }
  Trigger<uint32_t, uint64_t, uint16_t> *get_result_trigger() { return &result_trigger_; }
#ifdef USE_REFLEX_API_RULES
  void api_exchange(StringRef fabric, StringRef node, StringRef image, JsonObject reply);
#endif
  void setup() override;
  float get_setup_priority() const override { return setup_priority::LATE; }

 private:
  matter::MatterComponent *parent_{nullptr};
  binary_sensor::BinarySensor *active_sensor_{nullptr};
  Trigger<uint32_t, uint64_t, uint16_t> result_trigger_;
  sensor::Sensor *ready_sensor_{nullptr};
  sensor::Sensor *required_sensor_{nullptr};
  void publish_readiness();
  const char *transfer_key_{nullptr};
};

}  // namespace esphome::matter_reflex
