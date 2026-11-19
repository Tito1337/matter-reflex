#pragma once
#include "reflex_engine.h"
#include <lib/core/DataModelTypes.h>

namespace reflex_peers {
// Called under CHIP lock, including subscription and invocation callbacks.
void configure(chip::FabricIndex fabric, const reflex::Rules &rules);
struct Readiness { unsigned ready, total; };
Readiness readiness();
void tick();
void set_subscription_listener(void (*listener)(uint64_t node));
} // namespace reflex_peers
