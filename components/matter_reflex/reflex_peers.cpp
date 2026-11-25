#include "reflex_peers.h"
#include "reflex_runtime.h"
#include "reflex_peer_cache.h"

#include <app/CASEClientPool.h>
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
#include <platform/ConnectivityManager.h>
#if CHIP_DEVICE_CONFIG_ENABLE_THREAD
#include <platform/ThreadStackManager.h>
#include <openthread/netdata.h>
#include <openthread/thread.h>
#endif
#include <transport/SecureSession.h>
#include <esp_log.h>
#if CHIP_DEVICE_CONFIG_ENABLE_WIFI_STATION
#include <esp_netif.h>
#endif
#include <esp_timer.h>
#include <algorithm>
#include <array>
#include <cstdio>
#include <memory>
#include <new>

// CHIP exposes the server's CASE manager but no accessor for its client
// parameters. Observe its successful Init so cached connections use the same
// validation policy and providers, including application overrides.
namespace {
chip::CASEClientInitParams server_case_params;
}
extern "C" CHIP_ERROR real_case_init(chip::CASESessionManager *, chip::System::Layer *,
                                    const chip::CASESessionManagerConfig &) asm("__real__ZN4chip18CASESessionManager4InitEPNS_6System5LayerERKNS_24CASESessionManagerConfigE");
extern "C" CHIP_ERROR wrap_case_init(chip::CASESessionManager *, chip::System::Layer *,
                                    const chip::CASESessionManagerConfig &) asm("__wrap__ZN4chip18CASESessionManager4InitEPNS_6System5LayerERKNS_24CASESessionManagerConfigE");
extern "C" CHIP_ERROR wrap_case_init(chip::CASESessionManager *manager, chip::System::Layer *layer,
                                    const chip::CASESessionManagerConfig &config) {
    const auto error = real_case_init(manager, layer, config);
    if (manager == chip::Server::GetInstance().GetCASESessionManager())
        server_case_params = error == CHIP_NO_ERROR ? config.sessionInitParams : chip::CASEClientInitParams{};
    return error;
}

namespace reflex_peers {
using namespace chip;
using namespace chip::app;
namespace {
bool network_ready() {
#if CHIP_DEVICE_CONFIG_ENABLE_WIFI_STATION
    auto *station = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    esp_ip6_addr_t local;
    if (station && esp_netif_is_netif_up(station) &&
        esp_netif_get_ip6_linklocal(station, &local) == ESP_OK) return true;
#endif
#if CHIP_DEVICE_CONFIG_ENABLE_THREAD
    return DeviceLayer::ConnectivityMgr().IsThreadAttached();
#else
    return false;
#endif
}

CASEClientPool<CHIP_CONFIG_DEVICE_MAX_ACTIVE_CASE_CLIENTS> direct_clients;
struct ReleaseCASEClient {
    void operator()(CASEClient *client) const { direct_clients.Release(client); }
};
constexpr int64_t kSecond = 1000000;
constexpr uint16_t kNoSession = 0x100, kUnknownState = 0x101, kTransportError = 0x102;
FabricIndex fabric;
nvs_handle_t peer_storage;
reflex::PeerAddressCache address_cache;

void slot_key(size_t slot, char (&key)[12]) {
    std::snprintf(key, sizeof(key), "peer1_%u", static_cast<unsigned>(slot));
}

bool read_endpoint(size_t slot, reflex::PeerRecord &record) {
    char key[12];
    slot_key(slot, key);
    size_t size = reflex::kPeerRecordSize;
    return nvs_get_blob(peer_storage, key, &record, &size) == ESP_OK && size == reflex::kPeerRecordSize;
}

bool write_endpoint(size_t slot, const reflex::PeerRecord &record) {
    char key[12];
    slot_key(slot, key);
    auto error = nvs_set_blob(peer_storage, key, &record, reflex::kPeerRecordSize);
    if (error == ESP_OK) error = nvs_commit(peer_storage);
    if (error != ESP_OK) ESP_LOGW("reflex", "Peer address cache write failed: %s", esp_err_to_name(error));
    return error == ESP_OK;
}

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

class Peer : public ReadClient::Callback, public SessionEstablishmentDelegate {
public:
    NodeId node = 0;
    bool direct_buttons = false;
    bool buttons = false, lamp = false, level = false, cover = false, established = false, connecting = false;
    std::array<bool, 4> sensors{};
    static constexpr std::array<ClusterId, 4> sensor_clusters{0x45, 0x406, 0x402, 0x405};
    SessionHolder session;
    void restore_address() {
        if (const auto *endpoint = address_cache.lookup(node)) {
            const uint8_t *bytes = endpoint->ip.data();
            Inet::IPAddress ip;
            Inet::IPAddress::ReadAddress(bytes, ip);
            AddressResolve::ResolveResult hint;
            hint.address = Transport::PeerAddress::UDP(ip, endpoint->port);
            cached_address.SetValue(hint);
        }
    }

    void reset() {
        direct_client.reset();
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
    bool has_active_session() const { return session && session->IsActiveSession(); }
    void maintain(bool attached) {
        // CASE can become defunct while the subscription still awaits a report.
        if (reader && !has_active_session()) disconnect();
        if (!attached || reader || connecting || esp_timer_get_time() < retry_at) return;
        // A returning Thread router can attach before it receives Network Data.
        // Its mesh-local peers are already reachable. For other saved addresses,
        // give the NetData event one maintenance interval to arrive before Sigma1.
        const bool can_use_cache = cached_address.HasValue() && server_case_params.sessionManager;
        if (can_use_cache && !initial_route_ready()) return;
        connecting = true;
        attempts.start(can_use_cache, [this] { connect_direct(); }, [this] {
            Server::GetInstance().GetCASESessionManager()->FindOrEstablishSession(
                ScopedNodeId(node, fabric), &on_connected, &on_failure);
        });
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
        attempts.confirmed();
        remember_report_address();
        backoff = 1;
        if (subscription_listener) subscription_listener(node);
    }
    void NotifySubscriptionStillActive(const ReadClient &) override { remember_report_address(); }
    void OnDone(ReadClient *) override { disconnect(); }
private:
    std::unique_ptr<ReadClient> reader;
    Optional<EventNumber> high_water;
    Optional<AddressResolve::ResolveResult> cached_address;
    reflex::PeerConnectionAttempts attempts;
    std::unique_ptr<CASEClient, ReleaseCASEClient> direct_client;
    int64_t retry_at = 0;
    unsigned backoff = 1;
#if CHIP_DEVICE_CONFIG_ENABLE_THREAD
    int64_t network_data_deadline = 0;
#endif
    bool initial_route_ready() {
#if CHIP_DEVICE_CONFIG_ENABLE_THREAD
        const auto *endpoint = address_cache.lookup(node);
        if (retry_at || !endpoint) return true;
        const auto now = esp_timer_get_time();
        if (!network_data_deadline) network_data_deadline = now + kSecond / 2;
        if (now >= network_data_deadline) return true;

        auto &thread = DeviceLayer::ThreadStackMgrImpl();
        thread.LockThreadStack();
        auto *ot = thread.OTInstance();
        const bool ready = ot && (otNetDataGetLength(ot) ||
            std::equal(endpoint->ip.begin(), endpoint->ip.begin() + OT_MESH_LOCAL_PREFIX_SIZE,
                       otThreadGetMeshLocalPrefix(ot)->m8));
        thread.UnlockThreadStack();
        return ready;
#else
        return true;
#endif
    }

    void disconnect() {
        reader.reset();
        session.Release();
        established = connecting = false;
        forget_states(node);
        retry_at = esp_timer_get_time() + backoff * kSecond;
        backoff = std::min(backoff * 2, 30u);
    }
    void connect_direct() {
        auto &server = Server::GetInstance();
        const ScopedNodeId peer(node, fabric);
        auto existing = server.GetSecureSessionManager().FindSecureSessionForNode(
            peer, MakeOptional(Transport::SecureSession::Type::kCASE));
        if (existing.HasValue()) {
            connected(this, server.GetExchangeManager(), existing.Value());
            return;
        }
        // The address is already known. CASEClient sends Sigma1 directly, even
        // when Thread DNS-SD is still waiting for its SRP server to initialize.
        direct_client.reset(direct_clients.Allocate());
        if (!direct_client || server.GetExchangeManager().GetNumActiveExchanges() >= CHIP_CONFIG_MAX_EXCHANGE_CONTEXTS) {
            connection_failed(CHIP_ERROR_NO_MEMORY);
            return;
        }
        const auto &hint = cached_address.Value();
        auto err = direct_client->EstablishSession(server_case_params, peer, hint.address, hint.mrpRemoteConfig, this);
        if (err != CHIP_NO_ERROR) connection_failed(err);
    }
    void remember_report_address() {
        // ReadClient accepts reports on any CASE session with the publisher. CHIP
        // stamps the receiving session immediately before synchronous dispatch under
        // the stack lock. Skip ambiguous timestamps, including same-tick rotation.
        const ScopedNodeId peer(node, fabric);
        reflex::LatestPeerRx<Transport::SecureSession> received;
        Server::GetInstance().GetSecureSessionManager().ForEachMatchingSession(peer, [&](auto *secure) {
            if (secure->IsCASESession())
                received.observe(secure, secure->GetLastPeerActivityTime().count());
        });
        auto *secure = received.session();
        if (!secure || secure->GetLocalScopedNodeId().GetNodeId() != reflex_runtime::self_node()) return;
        const auto &address = secure->GetPeerAddress();
        if (address.GetTransportType() != Transport::Type::kUdp) return;
        reflex::PeerEndpoint endpoint{};
        uint8_t *bytes = endpoint.ip.data();
        address.GetIPAddress().WriteAddress(bytes);
        endpoint.port = address.GetPort();
        AddressResolve::ResolveResult hint;
        hint.address = address;
        hint.mrpRemoteConfig = secure->GetRemoteMRPConfig();
        cached_address.SetValue(hint);
        address_cache.remember(node, endpoint, write_endpoint);
    }
    void OnSessionEstablished(const SessionHandle &handle) override {
        direct_client.reset();
        connected(this, Server::GetInstance().GetExchangeManager(), handle);
    }
    void OnSessionEstablishmentError(CHIP_ERROR err, SessionEstablishmentStage) override { connection_failed(err); }
    void connection_failed(CHIP_ERROR err) {
        direct_client.reset();
        attempts.failed(err == CHIP_ERROR_NO_MEMORY);
        disconnect();
    }
    static void connected(void *ctx, Messaging::ExchangeManager &mgr, const SessionHandle &handle) {
        auto &p = *static_cast<Peer *>(ctx);
        p.connecting = false;
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
    static void failed(void *ctx, const ScopedNodeId &, CHIP_ERROR error) {
        static_cast<Peer *>(ctx)->connection_failed(error);
    }
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
            if (p) {
                p->node = node;
                p->restore_address();
            }
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
    const bool direct_button_toggle = rule.direct && rule.op == reflex::kNativeToggle && !reflex::sensor_cluster(rule.event);
    if (!session.HasValue() || !session.Value()->IsActiveSession() || (!target->established && !direct_button_toggle)) {
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
    case reflex::kNativeToggle:
        error = invoke(session.Value(), rule, number, run, Clusters::OnOff::Commands::Toggle::Type{});
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
        if (command.op == reflex::kNativeToggle || states[i].on != (command.op != 0)) states[i].on = -1;
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

void configure(FabricIndex index, const reflex::Rules &rules, nvs_handle_t storage) {
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
    end = std::unique(nodes.begin(), end);
    required_peers = end - nodes.begin();
    peer_storage = storage;
    address_cache.load(reflex_runtime::fabric_id(), reflex_runtime::self_node(), nodes.begin(), end, read_endpoint);
    for (size_t i = 0; i < rules.count; ++i) {
        states[i] = {-1, -1};
        sensor_states[i] = {};
        button_path_ready[i] = false;
    }
}
uint32_t direct_ready_mask() {
    uint32_t mask = 0;
    if (!rule_set || !network_ready()) return mask;
    for (size_t i = 0; i < rule_set->count; ++i) {
        const auto &rule = rule_set->rows[i];
        if (!rule.direct) continue;
        const auto *source = find(rule.source);
        const bool path_ready = reflex::sensor_cluster(rule.event) ? sensor_states[i].previous != -1 : button_path_ready[i];
        if (source && source->established && source->has_active_session() && path_ready)
            mask |= uint32_t(1) << i;
    }
    return mask;
}

Readiness readiness() {
    unsigned ready = 0;
    if (required_peers && network_ready()) {
        for (const auto &peer : peers)
            if (peer && peer->established && peer->has_active_session()) ++ready;
    }
    return {ready, required_peers};
}

void tick() {
    if (!fabric || !rule_set) return;
    const bool attached = network_ready();
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
    for (auto &peer : peers) if (peer) peer->maintain(attached);
}
} // namespace reflex_peers
