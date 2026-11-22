// Host adapter for the firmware engine. The Matter.js simulator owns the
// sessions and feeds actual subscription reports and a deterministic lease clock.
#include "reflex_engine.h"
#include "reflex_lease.h"
#include "reflex_peer_cache.h"
#include <arpa/inet.h>
#include <array>
#include <cassert>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#include <vector>

// The wire fixture learns the address from a working CASE session, then
// discards all sessions and discovery data. Return the firmware's first route
// after reloading its saved record into a fresh cache.
void cold_boot() {
    uint64_t fabric, owner, node;
    std::string address;
    reflex::PeerEndpoint endpoint{};
    std::cin >> fabric >> owner >> node >> address >> endpoint.port;
    assert(inet_pton(AF_INET6, address.c_str(), endpoint.ip.data()) == 1);
    reflex::PeerRecord record{};
    {
        reflex::PeerAddressCache cache;
        cache.load(fabric, owner, &node, &node + 1, [](size_t, auto &) { return false; });
        assert(cache.remember(node, endpoint, [&](size_t slot, const auto &saved) {
            assert(slot == 0);
            record = saved;
            return true;
        }));
    }
    reflex::PeerAddressCache restored;
    restored.load(fabric, owner, &node, &node + 1, [&](size_t slot, auto &saved) {
        if (slot != 0) return false;
        saved = record;
        return true;
    });
    auto *saved = restored.lookup(node);
    reflex::PeerConnectionAttempts attempts;
    attempts.start(saved != nullptr, [&] {
        char ip[INET6_ADDRSTRLEN];
        assert(inet_ntop(AF_INET6, saved->ip.data(), ip, sizeof(ip)));
        std::cout << ip << ' ' << saved->port;
    }, [] { std::cout << "discovery"; });
}

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
        if (operation == "cold_boot") {
            cold_boot();
        } else if (operation == "reset") {
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
            const bool expired = lease.expired(now);
            for (size_t i = 0; i < rules.count; ++i) {
                const auto &r = rules.rows[i];
                if (sensors[i].report(r, node, ep, cluster, value) && r.handles(expired)) {
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
