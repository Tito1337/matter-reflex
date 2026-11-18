#pragma once
#include "reflex_engine.h"
#include <lib/core/DataModelTypes.h>

namespace reflex_peers {
// Called under CHIP lock, including subscription and invocation callbacks.
void configure(chip::FabricIndex fabric, const reflex::Rules &rules);
void tick();
} // namespace reflex_peers
