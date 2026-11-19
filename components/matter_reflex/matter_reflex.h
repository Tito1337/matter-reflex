#pragma once

#include "esphome/core/component.h"
#include "esphome/components/matter/matter_component.h"

namespace esphome::matter_reflex {

class ReflexComponent : public Component {
 public:
  void register_endpoints(matter::MatterComponent *parent);
  void set_transfer_key(const char *key) { transfer_key_ = key; }
  void setup() override;
  float get_setup_priority() const override { return setup_priority::LATE; }

 private:
  matter::MatterComponent *parent_{nullptr};
  const char *transfer_key_{nullptr};
};

}  // namespace esphome::matter_reflex
