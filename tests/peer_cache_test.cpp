#include "reflex_peer_cache.h"
#include <cassert>
#include <cstring>
#include <map>

using namespace reflex;

int main() {
    std::map<size_t, std::array<uint8_t, kPeerRecordSize>> flash;
    unsigned writes = 0;
    auto read = [&](size_t slot, PeerRecord &record) {
        auto it = flash.find(slot);
        if (it == flash.end()) return false;
        std::memcpy(&record, it->second.data(), kPeerRecordSize);
        return true;
    };
    auto write = [&](size_t slot, const PeerRecord &record) {
        ++writes;
        std::memcpy(flash[slot].data(), &record, kPeerRecordSize);
        return true;
    };
    constexpr uint64_t nodes[] = {4, 23};
    constexpr uint64_t owner = 28, fabric = 0xfedcba9876543210ULL;
    PeerEndpoint endpoint{{0xfd, 0x12}, 5540};
    PeerAddressCache cache;
    cache.load(fabric, owner, nodes, nodes + 2, read);
    assert(!cache.lookup(4));
    assert(cache.remember(4, endpoint, write));
    assert(cache.remember(4, endpoint, write));
    assert(writes == 1);

    PeerAddressCache restarted;
    restarted.load(fabric, owner, nodes, nodes + 2, read);
    assert(restarted.lookup(4)->ip == endpoint.ip);
    assert(restarted.lookup(4)->port == 5540);
    assert(!restarted.lookup(23));
    assert(restarted.remember(4, endpoint, write) && writes == 1);

    // The resolver is still uninitialized after reboot. Only the direct CASE
    // transport can reach the peer. Neither route selection nor a failed CASE
    // attempt is allowed to change the endpoint on flash.
    PeerConnectionAttempts attempts;
    unsigned direct_calls = 0, discovery_calls = 0;
    auto direct = [&] {
        assert(restarted.lookup(4)->ip == endpoint.ip);
        ++direct_calls;
    };
    auto unavailable_resolver = [&] {
        ++discovery_calls;
        attempts.failed(false); // synchronous discovery-start failure
    };
    attempts.start(restarted.lookup(4) != nullptr, direct, unavailable_resolver);
    assert(direct_calls == 1 && discovery_calls == 0);
    attempts.failed(true); // first cached attempt could not obtain a CASE client
    attempts.start(true, direct, unavailable_resolver);
    assert(direct_calls == 2 && discovery_calls == 0);
    attempts.failed(false); // cached endpoint now timed out
    auto full_pool = [&] { ++discovery_calls; attempts.failed(true); };
    attempts.start(true, direct, full_pool);
    attempts.start(true, direct, unavailable_resolver);
    assert(direct_calls == 2 && discovery_calls == 2);
    attempts.start(true, direct, unavailable_resolver);
    attempts.confirmed();
    assert(direct_calls == 3 && discovery_calls == 2 && writes == 1);
    attempts.start(false, direct, unavailable_resolver);
    assert(direct_calls == 3 && discovery_calls == 3);

    // A publisher can rotate to another CASE session mid-subscription.
    // A tie cannot establish which session supplied the authenticated report.
    int original, rotated, newer;
    LatestPeerRx<int> received;
    assert(!received.session());
    received.observe(&original, 0);
    assert(received.session() == &original);
    received.observe(&rotated, 1);
    received.observe(&original, 0);
    assert(received.session() == &rotated);
    received.observe(&original, 1);
    assert(!received.session());
    received.observe(&newer, 2);
    assert(received.session() == &newer);

    PeerEndpoint changed = endpoint;
    changed.port = 5555;
    auto fail = [](size_t, const PeerRecord &) { return false; };
    assert(!restarted.remember(4, changed, fail));
    assert(restarted.lookup(4)->port == 5540);
    assert(restarted.remember(4, changed, write));
    changed.ip.back() = 17;
    assert(restarted.remember(4, changed, write));
    assert(restarted.remember(23, endpoint, write));
    assert(writes == 4 && flash.size() == 2);

    // Keep the lamp's slot when the switch is replaced. Reclaim the old slot.
    constexpr uint64_t changed_nodes[] = {9, 23};
    cache.load(fabric, owner, changed_nodes, changed_nodes + 2, read);
    assert(!cache.lookup(4) && !cache.lookup(9));
    assert(cache.lookup(23)->ip == endpoint.ip);
    assert(cache.remember(9, changed, write) && flash.size() == 2);
    restarted.load(fabric, owner, changed_nodes, changed_nodes + 2, read);
    assert(restarted.lookup(9)->ip == changed.ip && restarted.lookup(9)->port == 5555);

    cache.load(fabric + 1, owner, changed_nodes, changed_nodes + 2, read);
    assert(!cache.lookup(9));
    cache.load(fabric, owner + 1, changed_nodes, changed_nodes + 2, read);
    assert(!cache.lookup(9));
    cache.load(0, 0, nodes, nodes, read);
    assert(!cache.remember(4, endpoint, write));

    PeerEndpoint invalid{};
    invalid.port = 5540;
    assert(!invalid.usable());
    invalid.ip = {0xfe, 0x80};
    assert(!invalid.usable());
    invalid.ip = {0xff, 0x03};
    assert(!invalid.usable());
    invalid.ip = {0xfd, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0xff, 0xfe, 0, 0xab, 0xcd};
    assert(!invalid.usable());
    invalid = endpoint;
    invalid.port = 0;
    assert(!restarted.remember(9, invalid, write));
    // An invalid on-flash endpoint is ignored even with matching identities.
    write(0, {fabric, owner, 9, invalid});
    restarted.load(fabric, owner, changed_nodes, changed_nodes + 2, read);
    assert(!restarted.lookup(9));
    assert(restarted.lookup(23));
}
