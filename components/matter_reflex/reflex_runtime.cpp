#include "reflex_runtime.h"
#include "reflex_peers.h"
#include "reflex_lease.h"

#include <access/AccessControl.h>
#include <app/AttributeAccessInterfaceRegistry.h>
#include <app/CommandHandlerInterfaceRegistry.h>
#include <app/clusters/on-off-server/on-off-server.h>
#include <app-common/zap-generated/attributes/Accessors.h>
#include <app/EventLogging.h>
#include <clusters/Actions/Commands.h>
#include <clusters/Actions/Events.h>
#include <clusters/Actions/Structs.h>
#include <app/reporting/reporting.h>
#include <app/server/Server.h>
#include <esp_log.h>
#include <esp_timer.h>
#include <mbedtls/sha256.h>
#include <nvs.h>
#include <platform/CHIPDeviceLayer.h>
#include <cstring>
#include <cstdio>
#include <cinttypes>
#include <esp_random.h>

namespace reflex_runtime {
using namespace chip;
using namespace chip::app;
namespace {
constexpr EndpointId kEndpoint = 1;
constexpr ClusterId kCluster = 0xfff1fc00;
enum Attribute : AttributeId { Lease, Transfer, Hash, Active, Executions, JournalStart, DirectReady = JournalStart + kJournalPages };
constexpr char kHex[] = "0123456789abcdef";
nvs_handle_t storage;
reflex::LeaseDeadline deadline;
uint32_t execution_count;
struct Outcome { uint64_t run; uint32_t rule; uint16_t status; };
constexpr size_t kJournalSize = 8 * kJournalPages;
Outcome journal[kJournalSize];
size_t journal_write, journal_count;
uint32_t boot_nonce, run_sequence;
bool reported_active;
uint32_t reported_ready;
void (*active_changed)(bool);
ResultListener result_listener;
bool initialized;
FabricIndex fabric_index;
uint64_t fabric_identity;
NodeId local_node;
char digest[65];
reflex::Rules rules, candidate;
struct Record {
    uint64_t fabric;
    uint64_t node;
    uint8_t sha[32];
    uint8_t image[reflex::kMaxImage];
} record;
struct TransferBuffer {
    size_t expected = 0;
    size_t received = 0;
    char sha[65]{};
    uint8_t bytes[reflex::kMaxImage]{};
} staging;

const FabricInfo *single_fabric() {
    auto &table = Server::GetInstance().GetFabricTable();
    return table.FabricCount() == 1 ? &*table.begin() : nullptr;
}

void changed(AttributeId attr) {
    MatterReportingAttributeChangeCallback(kEndpoint, kCluster, attr);
}

int digit(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}
bool from_hex(const char *in, uint8_t *out, size_t bytes) {
    for (size_t i = 0; i < bytes; ++i) {
        int a = digit(in[2*i]), b = digit(in[2*i+1]);
        if (a < 0 || b < 0) return false;
        out[i] = (a << 4) | b;
    }
    return true;
}
bool hash_image(const uint8_t *data, size_t n, uint8_t sha[32], char hex[65]) {
    if (mbedtls_sha256(data, n, sha, 0) != 0) return false;
    for (size_t i = 0; i < 32; ++i) {
        hex[2*i] = kHex[sha[i] >> 4];
        hex[2*i+1] = kHex[sha[i] & 15];
    }
    hex[64] = 0;
    return true;
}

// Never reinterpret operational node IDs from a previous fabric. Store the
// root-key-derived compressed fabric ID and the local operational identity.
void select_fabric() {
    auto *f = single_fabric();
    FabricIndex index = f ? f->GetFabricIndex() : kUndefinedFabricIndex;
    uint64_t identity = f ? f->GetCompressedFabricId() : 0;
    NodeId node = f ? f->GetNodeId() : 0;
    if (index == fabric_index && identity == fabric_identity && node == local_node) return;
    fabric_index = index;
    fabric_identity = identity;
    local_node = node;
    deadline.reset(esp_timer_get_time());
    staging.expected = 0;
    journal_count = journal_write = 0;
    rules.count = 0;
    digest[0] = 0;
    size_t n = sizeof(record);
    constexpr size_t prefix = offsetof(Record, image);
    uint8_t sha[32];
    char hex[65];
    if (index && nvs_get_blob(storage, "rules", &record, &n) == ESP_OK &&
        n > prefix && n - prefix <= sizeof(record.image) &&
        record.fabric == identity && record.node == node &&
        hash_image(record.image, n-prefix, sha, hex) && memcmp(record.sha, sha, 32) == 0 &&
        reflex::decode(record.image, n-prefix, candidate)) {
        rules = candidate;
        memcpy(digest, hex, sizeof(digest));
    }
    reflex_peers::configure(index, rules, storage);
    changed(Hash);
}

bool owner_current() {
    auto *f = initialized ? single_fabric() : nullptr;
    return f && f->GetFabricIndex() == fabric_index &&
           f->GetCompressedFabricId() == fabric_identity && f->GetNodeId() == local_node;
}

void update_active();

bool renew_tenths(uint16_t tenths) {
    if (!owner_current() || !deadline.renew_tenths(esp_timer_get_time(), tenths)) return false;
    update_active();
    return true;
}

bool may_renew(CommandHandler &handler, const ConcreteCommandPath &path) {
    const auto subject = handler.GetSubjectDescriptor();
    const Access::RequestPath operation{path.mClusterId, path.mEndpointId,
        Access::RequestType::kCommandInvokeRequest, path.mCommandId};
    return owner_current() && subject.authMode == Access::AuthMode::kCase &&
           subject.fabricIndex == fabric_index &&
           Access::GetAccessControl().Check(subject, operation, Access::Privilege::kAdminister) == CHIP_NO_ERROR;
}

namespace Actions = Clusters::Actions;
constexpr uint16_t kRenewActionId = 1;
constexpr uint16_t kEndpointListId = 1;

class ActionLists : public AttributeAccessInterface {
public:
    ActionLists() : AttributeAccessInterface(MakeOptional(kEndpoint), Actions::Id) {}
    CHIP_ERROR Read(const ConcreteReadAttributePath &path, AttributeValueEncoder &encoder) override {
        if (path.mAttributeId == Actions::Attributes::ActionList::Id) {
            return encoder.EncodeList([](const auto &list) {
                const Actions::Structs::ActionStruct::Type action{
                    kRenewActionId, CharSpan::fromCharString("Renew Reflex lease"), Actions::ActionTypeEnum::kOther,
                    kEndpointListId, BitMask<Actions::CommandBits>(Actions::CommandBits::kInstantAction),
                    Actions::ActionStateEnum::kInactive};
                return list.Encode(action);
            });
        }
        if (path.mAttributeId == Actions::Attributes::EndpointLists::Id) {
            return encoder.EncodeList([](const auto &list) {
                constexpr EndpointId endpoints[]{kEndpoint};
                const Actions::Structs::EndpointListStruct::Type target{
                    kEndpointListId, CharSpan::fromCharString("Reflex controller"), Actions::EndpointListTypeEnum::kOther,
                    DataModel::List<const EndpointId>(endpoints)};
                return list.Encode(target);
            });
        }
        return CHIP_NO_ERROR; // Let ESP-Matter encode the globals.
    }
} action_lists;

template <typename Event>
void action_event(const Event &event) {
    EventNumber number;
    const auto err = LogEvent(event, kEndpoint, number);
    if (err != CHIP_NO_ERROR) ESP_LOGW("reflex", "Action event dropped: %" CHIP_ERROR_FORMAT, err.Format());
}

bool decode_instant_action(TLV::TLVReader &reader, Actions::Commands::InstantAction::DecodableType &request) {
    // The generated decoder defaults a missing mandatory ActionID to zero.
    TLV::TLVReader fields, id;
    TLV::TLVType outer;
    fields.Init(reader);
    return fields.GetType() == TLV::kTLVType_Structure && fields.EnterContainer(outer) == CHIP_NO_ERROR &&
           fields.FindElementWithTag(TLV::ContextTag(0), id) == CHIP_NO_ERROR &&
           DataModel::Decode(reader, request) == CHIP_NO_ERROR;
}

esp_err_t instant_action(const ConcreteCommandPath &path, TLV::TLVReader &reader, void *opaque) {
    using chip::Protocols::InteractionModel::Status;
    auto &handler = *static_cast<CommandHandler *>(opaque);
    Actions::Commands::InstantAction::DecodableType request;
    if (!may_renew(handler, path)) {
        handler.AddStatus(path, Status::UnsupportedAccess);
    } else if (!decode_instant_action(reader, request)) {
        handler.AddStatus(path, Status::InvalidCommand);
    } else if (request.actionID != kRenewActionId) {
        handler.AddStatus(path, Status::NotFound);
    } else {
        // The instantaneous action is the renewal itself. The deadline persists
        // after completion. InvokeID opts the caller into correlated events.
        const auto &invoke = request.invokeID;
        const bool renewed = lease(reflex::LeaseDeadline::kQuietSeconds);
        if (invoke.HasValue()) {
            if (renewed) {
                action_event(Actions::Events::StateChanged::Type{kRenewActionId, invoke.Value(),
                                                                Actions::ActionStateEnum::kInactive});
            } else {
                action_event(Actions::Events::ActionFailed::Type{kRenewActionId, invoke.Value(),
                    Actions::ActionStateEnum::kInactive, Actions::ActionErrorEnum::kUnknown});
            }
        }
        handler.AddStatus(path, renewed ? Status::Success : Status::Failure);
    }
    return ESP_OK; // The callback supplied the Interaction Model response.
}

class LegacyLeaseOutput : public CommandHandlerInterface {
public:
    LegacyLeaseOutput() : CommandHandlerInterface(MakeOptional<EndpointId>(2), Clusters::OnOff::Id) {}
    void InvokeCommand(HandlerContext &context) override {
        using namespace Clusters::OnOff::Commands;
        using chip::Protocols::InteractionModel::Status;
        // Older integrations use this command and supply their own duration.
        using Request = OnWithTimedOff::DecodableType;
        HandleCommand<Request>(context, [](HandlerContext &ctx, const Request &request) {
            auto &handler = ctx.mCommandHandler;
            const auto &path = ctx.mRequestPath;
            const bool heartbeat = request.onOffControl.Raw() == 0 && request.offWaitTime == 0 &&
                request.onTime > 0 && request.onTime <= 600 && may_renew(handler, path);
            OnOffServer::Instance().OnWithTimedOffCommand(&handler, path, request);
            if (!heartbeat) return;

            bool on = false;
            uint16_t remaining = 0;
            if (Clusters::OnOff::Attributes::OnOff::Get(path.mEndpointId, &on) == Status::Success && on &&
                Clusters::OnOff::Attributes::OnTime::Get(path.mEndpointId, &remaining) == Status::Success &&
                remaining >= request.onTime) {
                // A longer prior OnTime is valid for the outlet. It cannot
                // extend HA's authorization beyond the duration it requested.
                renew_tenths(request.onTime);
            }
        });
    }
} lease_output;
class Attributes : public AttributeAccessInterface {
public:
    Attributes() : AttributeAccessInterface(MakeOptional(kEndpoint), kCluster) {}
    CHIP_ERROR Read(const ConcreteReadAttributePath &p, AttributeValueEncoder &e) override {
        if (p.mAttributeId >= JournalStart && p.mAttributeId < JournalStart + kJournalPages) {
            char page[kJournalPageChars + 1];
            journal_page(p.mAttributeId - JournalStart, page);
            return e.Encode(CharSpan::fromCharString(page));
        }
        switch (p.mAttributeId) {
        case DirectReady: return e.Encode(direct_ready_mask());
        case Lease: return e.Encode(deadline.granted_seconds());
        case Transfer: return e.Encode(CharSpan());
        case Hash: return e.Encode(CharSpan::fromCharString(rules_hash()));
        case Active: return e.Encode(active());
        case Executions: return e.Encode(executions());
        default: return CHIP_NO_ERROR; // ESP-Matter serves global attributes.
        }
    }
    CHIP_ERROR Write(const ConcreteDataAttributePath &p, AttributeValueDecoder &d) override {
        auto &s = d.GetSubjectDescriptor();
        Access::RequestPath path{kCluster, kEndpoint, Access::RequestType::kAttributeWriteRequest, p.mAttributeId};
        if (!owner_current() || s.fabricIndex != fabric_index || s.authMode != Access::AuthMode::kCase ||
            Access::GetAccessControl().Check(s, path, Access::Privilege::kAdminister) != CHIP_NO_ERROR) {
            return CHIP_IM_GLOBAL_STATUS(UnsupportedAccess);
        }
        if (p.mAttributeId == Lease) {
            uint16_t value;
            ReturnErrorOnFailure(d.Decode(value));
            return lease(value) ? CHIP_NO_ERROR : CHIP_IM_GLOBAL_STATUS(ConstraintError);
        }
        if (p.mAttributeId == Transfer) {
            CharSpan frame;
            ReturnErrorOnFailure(d.Decode(frame));
            return transfer(frame.data(), frame.size()) ? CHIP_NO_ERROR : CHIP_IM_GLOBAL_STATUS(ConstraintError);
        }
        return CHIP_IM_GLOBAL_STATUS(UnsupportedWrite);
    }
} access;

void update_active() {
    bool now = active();
    if (now != reported_active) {
        reported_active = now;
        changed(Active);
        if (active_changed) active_changed(now);
    }
}
void thread_event(const DeviceLayer::ChipDeviceEvent *event, intptr_t) {
    if (event->Type == DeviceLayer::DeviceEventType::kThreadStateChange &&
        (event->ThreadStateChange.RoleChanged || event->ThreadStateChange.NetDataChanged)) {
        select_fabric();
        reflex_peers::tick();
    }
}

void timer(System::Layer *layer, void *) {
    select_fabric();
    update_active();
    reflex_peers::tick();
    const auto ready = direct_ready_mask();
    if (ready != reported_ready) {
        reported_ready = ready;
        changed(DirectReady);
    }
    ESP_ERROR_CHECK(layer->StartTimer(System::Clock::Milliseconds32(500), timer, nullptr) == CHIP_NO_ERROR ? ESP_OK : ESP_FAIL);
}
} // namespace

bool active() { return owner_current() && deadline.expired(esp_timer_get_time()); }
Handling handling() {
    if (!owner_current()) return Handling::unavailable;
    return deadline.expired(esp_timer_get_time()) ? Handling::all : Handling::direct_only;
}
uint32_t direct_ready_mask() {
    return owner_current() ? reflex_peers::direct_ready_mask() : 0;
}
const char *rules_hash() { return owner_current() ? digest : ""; }
uint32_t executions() { return execution_count; }
uint64_t self_node() { return owner_current() ? local_node : 0; }
uint64_t fabric_id() { return owner_current() ? fabric_identity : 0; }

bool lease(uint16_t seconds) {
    return seconds <= 60 && renew_tenths(seconds * 10);
}

bool replace_image(const uint8_t *data, size_t size) {
    if (!owner_current() || !data || size > sizeof(record.image) || !reflex::decode(data, size, candidate)) return false;
    char hex[65];
    if (!hash_image(data, size, record.sha, hex)) return false;
    if (strcmp(hex, digest) == 0) return true;
    record.fabric = fabric_identity;
    record.node = local_node;
    memcpy(record.image, data, size);
    if (nvs_set_blob(storage, "rules", &record, offsetof(Record, image)+size) != ESP_OK || nvs_commit(storage) != ESP_OK)
        return false;
    rules = candidate;
    journal_count = journal_write = 0;
    memcpy(digest, hex, sizeof(digest));
    reflex_peers::configure(fabric_index, rules, storage);
    changed(Hash);
    return true;
}

bool transfer(const char *frame, size_t length) {
    if (!owner_current() || !frame || !length) return false;
    if (frame[0] == 'B' && length == 69) {
        uint8_t n[2], sha[32];
        if (!from_hex(frame+1, sha, 32) || !from_hex(frame+65, n, 2)) return false;
        size_t expected = 256u*n[0] + n[1];
        if (expected < 8 || expected > sizeof(staging.bytes)) return false;
        memcpy(staging.sha, frame+1, 64);
        staging.sha[64] = 0;
        staging.expected = expected;
        staging.received = 0;
        return true;
    }
    if (!staging.expected) return false;
    if (frame[0] == 'D' && length > 5 && length <= 197 && (length-5)%2 == 0) {
        uint8_t o[2];
        const size_t bytes = (length-5)/2;
        if (!from_hex(frame+1, o, 2) || 256u*o[0]+o[1] != staging.received ||
            bytes > staging.expected-staging.received || !from_hex(frame+5, staging.bytes+staging.received, bytes))
            return false;
        staging.received += bytes;
        return true;
    }
    if (frame[0] == 'C' && length == 65 && staging.received == staging.expected &&
        memcmp(staging.sha, frame+1, 64) == 0) {
        char hex[65];
        uint8_t sha[32];
        if (!hash_image(staging.bytes, staging.expected, sha, hex) || strcmp(hex, staging.sha) != 0) return false;
        if (!replace_image(staging.bytes, staging.expected)) return false;
        staging.expected = 0;
        return true;
    }
    return false;
}

void create_cluster(esp_matter::endpoint_t *endpoint) {
    using namespace esp_matter;
    // Register the standard metadata directly. The SDK Actions delegate does
    // not receive the invoking subject needed for our administrator check.
    auto *actions = cluster::create(endpoint, Actions::Id, CLUSTER_FLAG_SERVER);
    ESP_ERROR_CHECK(actions &&
        cluster::global::attribute::create_feature_map(actions, 0) &&
        cluster::global::attribute::create_cluster_revision(actions, 1) &&
        cluster::actions::attribute::create_action_list(actions, nullptr, 0, 0) &&
        cluster::actions::attribute::create_endpoint_lists(actions, nullptr, 0, 0) &&
        cluster::actions::event::create_state_changed(actions) &&
        cluster::actions::event::create_action_failed(actions) ? ESP_OK : ESP_ERR_NO_MEM);
    ESP_ERROR_CHECK(command::create(actions, Actions::Commands::InstantAction::Id, COMMAND_FLAG_ACCEPTED,
                                    instant_action) ? ESP_OK : ESP_ERR_NO_MEM);
    auto *cluster = cluster::create(endpoint, kCluster, CLUSTER_FLAG_SERVER);
    ESP_ERROR_CHECK(cluster ? ESP_OK : ESP_ERR_NO_MEM);
    auto add = [cluster](uint32_t id, esp_matter_attr_val_t value, bool write = false, uint16_t capacity = 0) {
        auto flags = ATTRIBUTE_FLAG_READABLE | (write ? ATTRIBUTE_FLAG_WRITABLE : 0);
        ESP_ERROR_CHECK(attribute::create(cluster, id, flags, value, capacity) ? ESP_OK : ESP_ERR_NO_MEM);
    };
    char empty[] = "";
    add(0xfffc, esp_matter_uint32(0));
    add(0xfffd, esp_matter_uint16(1));
    add(Lease, esp_matter_uint16(0), true);
    add(Transfer, esp_matter_char_str(empty, 0), true, 197);
    add(Hash, esp_matter_char_str(empty, 0), false, 64);
    add(Active, esp_matter_bool(false));
    add(Executions, esp_matter_uint32(0));
    add(DirectReady, esp_matter_uint32(0));
    for (size_t i = 0; i < kJournalPages; ++i)
        add(JournalStart + i, esp_matter_char_str(empty, 0), false, kJournalPageChars);
    ESP_ERROR_CHECK(event::create(cluster, 0) ? ESP_OK : ESP_ERR_NO_MEM);
}

void start(void (*on_active_changed)(bool), ResultListener on_result) {
    result_listener = on_result;
    active_changed = on_active_changed;
    ESP_ERROR_CHECK(nvs_open("reflex", NVS_READWRITE, &storage));
    ESP_ERROR_CHECK(AttributeAccessInterfaceRegistry::Instance().Register(&action_lists) ? ESP_OK : ESP_FAIL);
    ESP_ERROR_CHECK(AttributeAccessInterfaceRegistry::Instance().Register(&access) ? ESP_OK : ESP_FAIL);
    ESP_ERROR_CHECK(CommandHandlerInterfaceRegistry::Instance().RegisterCommandHandler(&lease_output) == CHIP_NO_ERROR ? ESP_OK : ESP_FAIL);
    boot_nonce = esp_random();
    initialized = true;
    ESP_ERROR_CHECK(DeviceLayer::PlatformMgr().AddEventHandler(thread_event, 0) == CHIP_NO_ERROR ? ESP_OK : ESP_FAIL);
    timer(&DeviceLayer::SystemLayer(), nullptr);
}

struct Executed {
    static constexpr bool kIsFabricScoped = true;
    ClusterId GetClusterId() const { return kCluster; }
    EventId GetEventId() const { return 0; }
    FabricIndex GetFabricIndex() const { return fabric_index; }
    PriorityLevel GetPriorityLevel() const { return PriorityLevel::Info; }
    uint32_t rule;
    uint64_t number;
    uint16_t status;
    CHIP_ERROR Encode(TLV::TLVWriter &w, TLV::Tag tag) const {
        TLV::TLVType outer;
        ReturnErrorOnFailure(w.StartContainer(tag, TLV::kTLVType_Structure, outer));
        ReturnErrorOnFailure(w.Put(TLV::ContextTag(0), rule));
        ReturnErrorOnFailure(w.Put(TLV::ContextTag(1), local_node));
        ReturnErrorOnFailure(w.Put(TLV::ContextTag(2), number));
        ReturnErrorOnFailure(w.Put(TLV::ContextTag(3), status));
        ReturnErrorOnFailure(w.Put(TLV::ContextTag(254), fabric_index));
        return w.EndContainer(outer);
    }
};

uint64_t next_run() {
    if (++run_sequence == 0) { boot_nonce = esp_random(); ++run_sequence; }
    return (uint64_t(boot_nonce) << 32) | run_sequence;
}

void journal_page(size_t page, char *output) {
    *output = 0;
    if (!owner_current() || page >= kJournalPages) return;
    const size_t oldest = (journal_write + kJournalSize - journal_count) % kJournalSize;
    for (size_t i = page * 8; i < (page + 1) * 8 && i < journal_count; ++i) {
        const auto &item = journal[(oldest + i) % kJournalSize];
        snprintf(output, 29, "%016" PRIx64 "%08" PRIx32 "%04x", item.run, item.rule, unsigned(item.status));
        output += 28;
    }
}

void command_result(uint32_t rule, uint64_t event, uint16_t status, uint64_t run) {
    if (!owner_current()) return;
    if (run) {
        journal[journal_write] = {run, rule, status};
        journal_write = (journal_write + 1) % kJournalSize;
        if (journal_count < kJournalSize) ++journal_count;
    }
    ++execution_count;
    if (result_listener) result_listener(rule, event, status);
    changed(Executions);
    EventNumber n;
    auto err = LogEvent(Executed{rule, event, status}, kEndpoint, n);
    if (err != CHIP_NO_ERROR) ESP_LOGW("reflex", "Rule result event dropped: %" CHIP_ERROR_FORMAT, err.Format());
}
} // namespace reflex_runtime
