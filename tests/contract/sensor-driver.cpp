// Host adapter for the firmware engine. The Matter.js simulator owns the
// sessions and feeds actual subscription reports and a deterministic lease clock.
#include "reflex_engine.h"
#include "reflex_lease.h"
#include <array>
#include <cassert>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#include <vector>

int main(int argc, char **argv) {
    assert(argc == 2);
    std::ifstream input(argv[1], std::ios::binary);
    const std::vector<uint8_t> image(std::istreambuf_iterator<char>(input), {});
    reflex::Rules rules{};
    assert(reflex::decode(image.data(), image.size(), rules));
    std::array<reflex::SensorState, reflex::kMaxRules> sensors{};
    reflex::LeaseDeadline lease;
    lease.reset(0);
    std::string operation;
    while (std::cin >> operation) {
        if (operation == "reset") {
            sensors = {};
        } else if (operation == "renew") {
            int64_t now;
            std::cin >> now;
            assert(lease.renew_tenths(now, 100));
        } else {
            assert(operation == "report");
            int64_t now;
            uint64_t node;
            uint16_t ep;
            uint32_t cluster;
            int32_t value;
            std::cin >> now >> node >> ep >> cluster >> value;
            for (size_t i = 0; i < rules.count; ++i) {
                const auto &r = rules.rows[i];
                if (sensors[i].report(r, node, ep, cluster, value) && lease.expired(now)) {
                    reflex::Command cmd{};
                    assert(reflex::resolve(r, {-1, -1}, cmd));
                    std::cout << r.target << ' ' << r.target_ep << ' ' << int(cmd.op)
                              << ' ' << cmd.value << ' ' << r.transition;
                }
            }
        }
        std::cout << std::endl;
    }
}
