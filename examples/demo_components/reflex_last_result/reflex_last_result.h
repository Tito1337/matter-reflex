#pragma once

#include "esphome/components/matter_reflex/matter_reflex.h"
#include "esphome/components/text_sensor/text_sensor.h"
#include <cinttypes>
#include <cstdio>

namespace esphome::reflex_last_result {

class LastResult : public text_sensor::TextSensor {
 public:
  void subscribe(matter_reflex::ReflexComponent *parent) {
    parent->add_on_rule_result_callback([this](uint32_t rule, uint64_t event, uint16_t status) {
      char message[80];
      std::snprintf(message, sizeof(message), "rule=%08" PRIx32 " event=%" PRIu64 " status=0x%03x",
                    rule, event, static_cast<unsigned>(status));
      this->publish_state(message);
    });
  }
};

}  // namespace esphome::reflex_last_result
