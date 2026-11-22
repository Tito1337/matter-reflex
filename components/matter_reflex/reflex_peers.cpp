#include "reflex_peers.h"
#include "reflex_runtime.h"

#include <app/CASESessionManager.h>
#include <app/InteractionModelEngine.h>
#include <app/ReadClient.h>
#include <app/server/Server.h>
#include <clusters/LevelControl/Commands.h>
#include <clusters/OnOff/Commands.h>
#include <clusters/Switch/Events.h>
#include <clusters/WindowCovering/AttributeIds.h>
#include <clusters/WindowCovering/Commands.h>
#include <controller/InvokeInteraction.h>
#include <transport/SecureSession.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <algorithm>
#include <array>
#include <memory>
#include <new>

namespace reflex_peers {
using namespace chip;
using namespace chip::app;
namespace {
constexpr int64_t kSecond = 1000000;
constexpr uint16_t kNoSession = 0x100, kUnknownState = 0x101, kTransportError = 0x102;
FabricIndex fabric;
uint32_t generation;
void (*subscription_listener)(uint64_t);
const reflex::Rules *rule_set;
unsigned required_peers;
reflex::SensorState sensor_states[reflex::kMaxRules];
bool button_path_ready[reflex::kMaxRules];
reflex::State states[reflex::kMaxRules];

void forget_states(NodeId node) {
    for (size_t i = 0; i < rule_set->count; ++i) {
        if (rule_set->rows[i].source == node) {
            sensor_states[i] = {};
            button_path_ready[i] = false;
        }
        if (rule_set->rows[i].target == node) states[i] = {-1, -1};
    }
}

void dispatch(size_t index, EventNumber number, uint64_t run);

int32_t measurement(ClusterId cluster, TLV::TLVReader *reader, const StatusIB &status) {
    if (!reader || status.ToChipError() != CHIP_NO_ERROR) return reflex::kInvalidMeasurement;
    switch (cluster) {
    case 0x45: {
        bool value;
        if (reader->Get(value) == CHIP_NO_ERROR) return value;
        break;
    }
    case 0x406: {
        uint8_t value;
        if (reader->Get(value) == CHIP_NO_ERROR) return value;
        break;
    }
    case 0x402: {
        int16_t value;
        if (reader->Get(value) == CHIP_NO_ERROR) return value;
        break;
    }
    case 0x405: {
        uint16_t value;
        if (reader->Get(value) == CHIP_NO_ERROR) return value;
        break;
    }
    }
    return reflex::kInvalidMeasurement;
}

class Peer : public ReadClient::Callback {
public:
    NodeId node = 0;
    bool direct_buttons = false;
    bool buttons = false, lamp = false, level = false, cover = false, established = false, connecting = false;
    std::array<bool, 4> sensors{};
    static constexpr std::array<ClusterId, 4> sensor_clusters{0x45, 0x406, 0x402, 0x405};
    SessionHolder session;
    void reset() {
        on_connected.Cancel();
        on_failure.Cancel();
        reader.reset();
        session.Release();
        node = 0;
        direct_buttons = buttons = lamp = level = cover = established = connecting = false;
        sensors.fill(false);
        high_water.ClearValue();
        cached_address.ClearValue();
        retry_at = 0;
        backoff = 1;
    }
    void maintain() {
        if (reader) {
            auto handle = session.Get();
            // CHIP can mark CASE defunct after an invoke times out while the
            // read client still waits for the next subscription report.
            if (!handle.HasValue() || !handle.Value()->IsActiveSession()) disconnect();
        }
        if (reader || connecting || esp_timer_get_time() < retry_at) return;
        connecting = true;
        Server::GetInstance().GetCASESessionManager()->FindOrEstablishSession(
            ScopedNodeId(node, fabric), &on_connected, &on_failure,
#if CHIP_DEVICE_CONFIG_ENABLE_AUTOMATIC_CASE_RETRIES
            1, nullptr,
#endif
            TransportPayloadCapability::kMRPPayload, cached_address);
    }
    void OnAttributeData(const ConcreteDataAttributePath &p, TLV::TLVReader *r, const StatusIB &s) override {
        if (p.mAttributeId != 0) return;
        if (p.mClusterId == 0x3b) {
            uint8_t positions;
            const bool valid = r && s.ToChipError() == CHIP_NO_ERROR &&
                               r->Get(positions) == CHIP_NO_ERROR && positions >= 2;
            for (size_t i = 0; i < rule_set->count; ++i) {
                const auto &rule = rule_set->rows[i];
                if (rule.source == node && rule.source_ep == p.mEndpointId)
                    button_path_ready[i] = valid;
            }
            return;
        }
        if (p.mClusterId != 6 && p.mClusterId != 8) {
            const auto value = measurement(p.mClusterId, r, s);
            const auto handling = reflex_runtime::handling();
            uint64_t run = 0;
            for (size_t i = 0; i < rule_set->count; ++i) {
                const auto &rule = rule_set->rows[i];
                // Update while HA owns the lease, too. Expiry never replays an edge.
                if (sensor_states[i].report(rule, node, p.mEndpointId, p.mClusterId, value) &&
                    established && handling != reflex_runtime::Handling::unavailable &&
                    rule.handles(handling == reflex_runtime::Handling::all)) {
                    if (!run) run = reflex_runtime::next_run();
                    dispatch(i, 0, run); // Attribute reports have no Matter EventNumber.
                }
            }
            return;
        }
        int value = -1;
        if (r && s.ToChipError() == CHIP_NO_ERROR) {
            bool on;
            uint8_t level;
            if (p.mClusterId == 6 && r->Get(on) == CHIP_NO_ERROR) value = on;
            if (p.mClusterId == 8 && r->Get(level) == CHIP_NO_ERROR && level <= 254) value = level;
        }
        for (size_t i = 0; i < rule_set->count; ++i) {
            const auto &rule = rule_set->rows[i];
            if (rule.target == node && rule.target_ep == p.mEndpointId) {
                (p.mClusterId == 6 ? states[i].on : states[i].level) = value;
            }
        }
    }
    void OnEventData(const EventHeader &, TLV::TLVReader *, const StatusIB *) override;
    void OnSubscriptionEstablished(SubscriptionId) override {
        established = true;
        backoff = 1;
        if (subscription_listener) subscription_listener(node);
    }
    void OnDone(ReadClient *) override { disconnect(); }
private:
    std::unique_ptr<ReadClient> reader;
    Optional<EventNumber> high_water;
    Optional<AddressResolve::ResolveResult> cached_address;
    int64_t retry_at = 0;
    unsigned backoff = 1;
    void disconnect() {
        reader.reset();
        session.Release();
        established = connecting = false;
        forget_states(node);
        retry_at = esp_timer_get_time() + backoff * kSecond;
        backoff = std::min(backoff * 2, 30u);
    }
    static void connected(void *ctx, Messaging::ExchangeManager &mgr, const SessionHandle &handle) {
        auto &p = *static_cast<Peer *>(ctx);
        p.connecting = false;
        // CHIP retries CASE at this authenticated Thread peer address if DNS-SD
        // resolution fails after loss of the border router. Store no fabric secrets.
        if (auto *secure = handle->AsSecureSession()) {
            AddressResolve::ResolveResult last;
            last.address = secure->GetPeerAddress();
            last.mrpRemoteConfig = secure->GetRemoteMRPConfig();
            p.cached_address.SetValue(last);
        }
        p.reader.reset(new (std::nothrow) ReadClient(InteractionModelEngine::GetInstance(), &mgr, p,
                                                    ReadClient::InteractionType::Subscribe));
        if (!p.reader || !p.session.Grab(handle)) { p.disconnect(); return; }
        EventPathParams events(kInvalidEndpointId, 0x3b, kInvalidEventId, true /* urgent */);
        AttributePathParams attrs[4 + sensor_clusters.size()];
        size_t attr_count = 0;
        for (size_t i = 0; i < p.sensors.size(); ++i)
            if (p.sensors[i]) attrs[attr_count++] = AttributePathParams(kInvalidEndpointId, sensor_clusters[i], 0);
        // NumberOfPositions is mandatory and stable on each Switch endpoint.
        if (p.direct_buttons) attrs[attr_count++] = AttributePathParams(kInvalidEndpointId, 0x3b, 0);
        if (p.lamp) attrs[attr_count++] = AttributePathParams(kInvalidEndpointId, 6, 0);
        if (p.level) {
            attrs[attr_count++] = AttributePathParams(kInvalidEndpointId, 8, 0);
        }
        if (p.cover) {
            // All cover commands are absolute. Keep a CASE subscription to a
            // mandatory stable attribute without requesting movement reports.
            attrs[attr_count++] = AttributePathParams(kInvalidEndpointId, Clusters::WindowCovering::Id,
                                                      Clusters::WindowCovering::Attributes::FeatureMap::Id);
        }
        ReadPrepareParams req(handle);
        if (p.buttons) {
            req.mpEventPathParamsList = &events;
            req.mEventPathParamsListSize = 1;
            if (p.high_water.HasValue()) req.mEventNumber.SetValue(p.high_water.Value()+1);
        }
        if (attr_count) {
            req.mpAttributePathParamsList = attrs;
            req.mAttributePathParamsListSize = attr_count;
        }
        req.mMinIntervalFloorSeconds = 0;
        // Negotiate the same liveness window with all publishers, including ICDs.
        req.mMaxIntervalCeilingSeconds = 60;
        // One subscription per peer. Replace previous subscriptions from our
        // node, even if power was lost. Other subscriber nodes keep theirs.
        req.mKeepSubscriptions = false;
        if (p.reader->SendRequest(req) != CHIP_NO_ERROR) p.disconnect();
    }
    static void failed(void *ctx, const ScopedNodeId &, CHIP_ERROR) { static_cast<Peer *>(ctx)->disconnect(); }
    chip::Callback::Callback<OnDeviceConnected> on_connected{connected, this};
    chip::Callback::Callback<OnDeviceConnectionFailure> on_failure{failed, this};
};

// One subscription per physical node, including multi-endpoint BILRESA. Store
// a stable Callback object while CASE is pending. Allocate peers on demand.
std::unique_ptr<Peer> peers[2*reflex::kMaxRules];

Peer *find(NodeId node) {
    for (auto &p : peers) if (p && p->node == node) return p.get();
    return nullptr;
}
Peer *ensure(NodeId node) {
    if (auto *p = find(node)) return p;
    for (auto &p : peers) {
        if (!p) {
            p.reset(new (std::nothrow) Peer);
            if (p) p->node = node;
            return p.get();
        }
    }
    return nullptr;
}

template<typename Request>
CHIP_ERROR invoke(const SessionHandle &session, const reflex::Rule &rule, EventNumber number, uint64_t run, const Request &request) {
    const auto id = rule.id;
    const auto revision = generation;
    auto done = [revision, id, number, run](uint16_t status) {
        if (revision == generation) reflex_runtime::command_result(id, number, status, run);
    };
    auto success = [done](const ConcreteCommandPath &, const StatusIB &status, const auto &) {
        done(static_cast<uint16_t>(status.mStatus));
    };
    auto failure = [done](CHIP_ERROR e) {
        done(e.IsIMStatus() ? static_cast<uint16_t>(StatusIB(e).mStatus) : kTransportError);
    };
    return Controller::InvokeCommandRequest(&Server::GetInstance().GetExchangeManager(), session,
                                           rule.target_ep, request, success, failure);
}

void dispatch(size_t index, EventNumber number, uint64_t run) {
    const auto &rule = rule_set->rows[index];
    auto *target = find(rule.target);
    auto session = target ? target->session.Get() : Optional<SessionHandle>();
    if (!target || !target->established || !session.HasValue() || !session.Value()->IsActiveSession()) {
        reflex_runtime::command_result(rule.id, number, kNoSession, run);
        return;
    }
    reflex::Command command{};
    if (!reflex::resolve(rule, states[index], command)) {
        reflex_runtime::command_result(rule.id, number, kUnknownState, run);
        return;
    }
    CHIP_ERROR error;
    namespace covering = Clusters::WindowCovering::Commands;
    switch (command.op) {
    case 0:
        error = invoke(session.Value(), rule, number, run, Clusters::OnOff::Commands::Off::Type{});
        break;
    case 1:
        error = invoke(session.Value(), rule, number, run, Clusters::OnOff::Commands::On::Type{});
        break;
    case 3: {
        Clusters::LevelControl::Commands::MoveToLevelWithOnOff::Type request;
        request.level = static_cast<uint8_t>(command.value);
        request.transitionTime.SetNonNull(rule.transition);
        error = invoke(session.Value(), rule, number, run, request);
        break;
    }
    case reflex::kTiltOpen:
    case reflex::kCoverOpen:
        error = invoke(session.Value(), rule, number, run, covering::UpOrOpen::Type{});
        break;
    case reflex::kTiltClose:
    case reflex::kCoverClose:
        error = invoke(session.Value(), rule, number, run, covering::DownOrClose::Type{});
        break;
    case reflex::kTiltStop:
    case reflex::kCoverStop:
        error = invoke(session.Value(), rule, number, run, covering::StopMotion::Type{});
        break;
    case reflex::kCoverPosition: {
        covering::GoToLiftPercentage::Type request;
        request.liftPercent100thsValue = command.value;
        error = invoke(session.Value(), rule, number, run, request);
        break;
    }
    case reflex::kTiltPosition: {
        covering::GoToTiltPercentage::Type request;
        request.tiltPercent100thsValue = command.value;
        error = invoke(session.Value(), rule, number, run, request);
        break;
    }
    default:
        error = CHIP_ERROR_INVALID_ARGUMENT;
    }
    if (error != CHIP_NO_ERROR) {
        reflex_runtime::command_result(rule.id, number, kTransportError, run);
        return;
    }
    if (reflex::is_cover(command.op)) return;
    // Invalidate only fields expected to change. Wait for a fresh lamp report
    // before resolving another relative action against an obsolete value.
    for (size_t i = 0; i < rule_set->count; ++i) {
        const auto &other = rule_set->rows[i];
        if (other.target != rule.target || other.target_ep != rule.target_ep) continue;
        if (states[i].on != (command.op != 0)) states[i].on = -1;
        if (command.op == 3 && states[i].level != command.value) states[i].level = -1;
    }
}

void Peer::OnEventData(const EventHeader &h, TLV::TLVReader *r, const StatusIB *s) {
    if (!r || (s && s->ToChipError() != CHIP_NO_ERROR) || h.mPath.mClusterId != 0x3b ||
        (high_water.HasValue() && h.mEventNumber <= high_water.Value())) return;
    high_water.SetValue(h.mEventNumber);
    // Initial and reconnect priming reports may contain historical button
    // presses, including ones already handled by HA before the outage.
    if (!established || h.mPath.mEventId > 6) return;
    uint8_t presses = 0;
    if (h.mPath.mEventId == Clusters::Switch::Events::MultiPressComplete::Id) {
        Clusters::Switch::Events::MultiPressComplete::DecodableType value;
        if (value.Decode(*r) != CHIP_NO_ERROR) return;
        presses = value.totalNumberOfPressesCounted;
    }
    const auto handling = reflex_runtime::handling();
    if (handling == reflex_runtime::Handling::unavailable) return;
    const uint64_t run = reflex_runtime::next_run();
    for (size_t i = 0; i < rule_set->count; ++i) {
        if ((handling == reflex_runtime::Handling::all || button_path_ready[i]) &&
            rule_set->rows[i].handles(handling == reflex_runtime::Handling::all) &&
            reflex::matches(rule_set->rows[i], node, h.mPath.mEndpointId, h.mPath.mEventId, presses))
            dispatch(i, h.mEventNumber, run);
    }
}
} // namespace

void set_subscription_listener(void (*listener)(uint64_t)) {
    subscription_listener = listener;
}

void configure(FabricIndex index, const reflex::Rules &rules) {
    ++generation;
    for (auto &p : peers) if (p) { p->reset(); p.reset(); }
    fabric = index;
    rule_set = &rules;
    std::array<NodeId, 2 * reflex::kMaxRules> nodes;
    auto end = nodes.begin();
    for (size_t i = 0; i < rules.count; ++i) {
        *end++ = rules.rows[i].source;
        *end++ = rules.rows[i].target;
    }
    std::sort(nodes.begin(), end);
    required_peers = std::unique(nodes.begin(), end) - nodes.begin();
    for (size_t i = 0; i < rules.count; ++i) {
        states[i] = {-1, -1};
        sensor_states[i] = {};
        button_path_ready[i] = false;
    }
}
uint32_t direct_ready_mask() {
    uint32_t mask = 0;
    if (!rule_set) return mask;
    for (size_t i = 0; i < rule_set->count; ++i) {
        const auto &rule = rule_set->rows[i];
        if (!rule.direct) continue;
        const auto *source = find(rule.source);
        const bool path_ready = reflex::sensor_cluster(rule.event) ? sensor_states[i].previous != -1 : button_path_ready[i];
        if (source && source->established && path_ready)
            mask |= uint32_t(1) << i;
    }
    return mask;
}

Readiness readiness() {
    unsigned ready = 0;
    for (const auto &peer : peers)
        if (peer && peer->established) ++ready;
    return {ready, required_peers};
}

void tick() {
    if (!fabric || !rule_set) return;
    for (size_t i = 0; i < rule_set->count; ++i) {
        const auto &rule = rule_set->rows[i];
        if (auto *p = ensure(rule.source)) {
            auto cluster = reflex::sensor_cluster(rule.event);
            if (!cluster) {
                p->buttons = true;
                p->direct_buttons |= rule.direct;
            } else for (size_t j = 0; j < p->sensors.size(); ++j)
                if (p->sensor_clusters[j] == cluster) p->sensors[j] = true;
        }
        if (auto *p = ensure(rule_set->rows[i].target)) {
            if (reflex::is_cover(rule_set->rows[i].op)) p->cover = true;
            else {
                p->lamp = true;
                if (rule.op >= 3 && rule.op <= 5) p->level = true;
            }
        }
    }
    for (auto &peer : peers) if (peer) peer->maintain();
}
} // namespace reflex_peers
