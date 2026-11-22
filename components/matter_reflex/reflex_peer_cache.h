#pragma once

#include "reflex_engine.h"
#include <algorithm>
#include <array>

namespace reflex {

// Keep the selected route on an admission failure. A real failure alternates
// cached CASE with a complete discovery attempt. Callbacks may run inline.
class PeerConnectionAttempts {
public:
    template<class Direct, class Discover>
    void start(bool cached, Direct direct, Discover discover) {
        using_cache_ = cached && prefer_cache_;
        if (using_cache_) direct();
        else discover();
    }
    void failed(bool out_of_resources) {
        if (!out_of_resources) prefer_cache_ = !using_cache_;
    }
    void confirmed() { prefer_cache_ = true; }
private:
    bool prefer_cache_ = true, using_cache_ = false;
};

template<class Session>
class LatestPeerRx {
public:
    void observe(Session *session, uint64_t timestamp) {
        if (!seen_ || timestamp > latest_) {
            seen_ = true;
            latest_ = timestamp;
            session_ = session;
        } else if (timestamp == latest_) {
            session_ = nullptr;
        }
    }
    Session *session() const { return session_; }
private:
    bool seen_ = false;
    uint64_t latest_ = 0;
    Session *session_ = nullptr;
};

struct PeerEndpoint {
    std::array<uint8_t, 16> ip;
    uint16_t port;

    bool usable() const {
        // Persist routable IPv6 unicast only. Thread RLOCs and anycast
        // locators identify a position in the mesh, not a durable endpoint.
        const bool unicast = (ip[0] & 0xe0) == 0x20 || (ip[0] & 0xfe) == 0xfc;
        const bool locator = ip[8] == 0 && ip[9] == 0 && ip[10] == 0 &&
                             ip[11] == 0xff && ip[12] == 0xfe && ip[13] == 0;
        return port && unicast && !locator;
    }
};

// One independent NVS blob per occupied slot. The version is in the key.
// Only serialize through the port field (exclude any trailing object padding).
struct PeerRecord {
    uint64_t fabric, owner, node;
    PeerEndpoint endpoint;
};
constexpr size_t kPeerRecordSize = offsetof(PeerRecord, endpoint) + offsetof(PeerEndpoint, port) + sizeof(uint16_t);
static_assert(kPeerRecordSize == 42);

class PeerAddressCache {
public:
    // Keep slots for peers still present when rules change. All other slots
    // can be reused, even after recommissioning or an interrupted update.
    template<class Read>
    void load(uint64_t fabric, uint64_t owner, const uint64_t *begin, const uint64_t *end, Read read) {
        fabric_ = fabric;
        owner_ = owner;
        slots_.fill({});
        if (!owner || begin == end) return;
        for (size_t i = 0; i < slots_.size(); ++i) {
            PeerRecord record{};
            if (read(i, record) && record.fabric == fabric && record.owner == owner &&
                std::binary_search(begin, end, record.node) && record.endpoint.usable()) {
                slots_[i] = {record.node, record.endpoint};
            }
        }
    }

    const PeerEndpoint *lookup(uint64_t node) const {
        for (const auto &slot : slots_)
            if (slot.node == node && node) return &slot.endpoint;
        return nullptr;
    }

    template<class Write>
    bool remember(uint64_t node, const PeerEndpoint &endpoint, Write write) {
        if (!owner_ || !node || !endpoint.usable()) return false;
        auto available = slots_.end();
        for (auto slot = slots_.begin(); slot != slots_.end(); ++slot) {
            if (slot->node == node) {
                if (slot->endpoint.ip == endpoint.ip && slot->endpoint.port == endpoint.port) return true;
                available = slot;
                break;
            }
            if (slot->node == 0 && available == slots_.end()) available = slot;
        }
        if (available == slots_.end() ||
            !write(available - slots_.begin(), PeerRecord{fabric_, owner_, node, endpoint})) return false;
        *available = {node, endpoint};
        return true;
    }

private:
    struct Slot { uint64_t node; PeerEndpoint endpoint; };
    std::array<Slot, 2 * kMaxRules> slots_{};
    uint64_t fabric_ = 0, owner_ = 0;
};
} // namespace reflex
