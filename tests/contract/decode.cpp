// Read the image emitted by the integration, then resolve its four actions.
#include "reflex_engine.h"
#include <algorithm>
#include <array>
#include <cassert>
#include <iostream>
#include <iterator>
#include <vector>

int main() {
    const std::vector<uint8_t> image(std::istreambuf_iterator<char>(std::cin), {});
    reflex::Rules rules{};
    assert(reflex::decode(image.data(), image.size(), rules));
    assert(rules.count == 4);
    std::array<bool, 4> seen{};
    for (size_t i = 0; i < rules.count; ++i) {
        const auto &r = rules.rows[i];
        assert(reflex::matches(r, 0x123456789ABCDEF0ULL, 2, 6, 2));
        assert(r.target_ep == 1 && r.target >= 40 && r.target <= 43);
        assert(r.direct == (r.target == 41));
        seen[r.target - 40] = true;
        reflex::Command command{};
        assert(reflex::resolve(r, {1, 200}, command));
        switch (r.target) {
        case 40: assert(command.op == 3 && command.value == 178); break;
        case 41: assert(r.op == 2 && command.op == 0); break;
        case 42: assert(command.op == reflex::kCoverPosition && command.value == 2500); break;
        case 43: assert(r.op == 4 && r.value == -128 && command.op == 3 && command.value < 200); break;
        }
    }
    assert(std::all_of(seen.begin(), seen.end(), [](bool value) { return value; }));
}
