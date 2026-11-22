#include "reflex_engine.h"
#include <algorithm>
#include <cstring>
#include <cmath>

namespace reflex {
namespace {
uint64_t read_be(const uint8_t *data, size_t size) {
    uint64_t value = 0;
    for (size_t i = 0; i < size; ++i) value = (value << 8) | data[i];
    return value;
}
bool operational(uint64_t id) { return id && id <= 0xffffffefffffffffULL; }
bool valid_operand(uint8_t op, int16_t value) {
    switch (op) {
    case 0: case 1: case 2:
    case kCoverOpen: case kCoverClose: case kCoverStop:
    case kTiltOpen: case kTiltClose: case kTiltStop: return value == 0;
    case 3: return value >= 0 && value <= 255;
    case 4: return value >= -255 && value <= 255;
    case 5: return value >= -100 && value <= 100;
    case kCoverPosition: case kTiltPosition: return value >= 0 && value <= 100;
    default: return false;
    }
}
int rounded(int num, int den) {
    const int q = num / den, r = num % den;
    return q + ((2 * r > den) || (2 * r == den && (q & 1)));
}
}
uint32_t sensor_cluster(uint8_t event) {
    switch (event) {
    case kBooleanFalse: case kBooleanTrue: return 0x45;
    case kUnoccupied: case kOccupied: return 0x406;
    case kTemperatureAbove: case kTemperatureBelow: return 0x402;
    case kHumidityAbove: case kHumidityBelow: return 0x405;
    default: return 0;
    }
}
bool SensorState::report(const Rule &rule, uint64_t node, uint16_t ep, uint32_t cluster, int32_t value) {
    if (rule.source != node || rule.source_ep != ep || !cluster || sensor_cluster(rule.event) != cluster)
        return false;
    int8_t current = -1;
    switch (cluster) {
    case 0x45:
        if (value == 0 || value == 1) current = value == (rule.event == kBooleanTrue);
        break;
    case 0x406:
        if (value >= 0 && value <= 255) current = (value & 1) == (rule.event == kOccupied);
        break;
    case 0x402:
        if (value >= -27315 && value <= 32767)
            current = rule.event == kTemperatureAbove ? value > rule.threshold : value < rule.threshold;
        break;
    case 0x405:
        if (value >= 0 && value <= 10000)
            current = rule.event == kHumidityAbove ? value > rule.threshold : value < rule.threshold;
        break;
    }
    bool crossed = previous == 0 && current == 1;
    previous = current;
    return crossed;
}
bool decode(const uint8_t *data, size_t len, Rules &out) {
    if (!data || len < 8 || std::memcmp(data, "MRFX", 4) || (data[4] != 1 && data[4] != 2) ||
        data[5] > kMaxRules || data[6] || data[7]) return false;
    const size_t stride = data[4] == 1 ? 32 : 36;
    if (len != 8 + stride * data[5]) return false;
    Rules next{};
    for (size_t i = 0; i < data[5]; ++i) {
        const uint8_t *p = data + 8 + stride * i;
        Rule &r = next.rows[i];
        r.id = read_be(p, 4);
        r.source = read_be(p + 4, 8);
        r.source_ep = read_be(p + 12, 2);
        r.event = p[14]; r.presses = p[15];
        r.target = read_be(p + 16, 8);
        r.target_ep = read_be(p + 24, 2);
        r.op = p[26];
        const int raw = read_be(p + 27, 2);
        r.value = raw >= 32768 ? raw - 65536 : raw;
        r.min_level = p[29]; r.max_level = p[30];
        if (data[4] == 2) {
            const int threshold = read_be(p + 32, 2);
            r.threshold = threshold >= 32768 ? threshold - 65536 : threshold;
            r.transition = read_be(p + 34, 2);
            if ((r.event < kTemperatureAbove && r.threshold != 0) ||
                (sensor_cluster(r.event) == 0x402 && r.threshold < -27315) ||
                (sensor_cluster(r.event) == 0x405 && (r.threshold < 0 || r.threshold > 10000)) ||
                (r.transition && (r.op < 3 || r.op > 5 || r.transition == 0xffff))) return false;
        } else if (r.event > 6 || r.op > kCoverPosition) return false;
        if (!r.id || !operational(r.source) || !operational(r.target) ||
            !r.source_ep || r.source_ep == 0xffff || !r.target_ep || r.target_ep == 0xffff ||
            r.event > kHumidityBelow || r.event == 5 ||
            (r.event == 6 ? (r.presses < 1 || r.presses > 8) : r.presses != 0) ||
            !valid_operand(r.op, r.value) || r.min_level < 1 ||
            r.min_level >= r.max_level || r.max_level > 254 || p[31]) return false;
        for (size_t j = 0; j < i; ++j) if (next.rows[j].id == r.id) return false;
    }
    next.count = data[5];
    out = next;
    return true;
}
bool matches(const Rule &r, uint64_t source, uint16_t ep, uint8_t event, uint8_t presses) {
    return r.source == source && r.source_ep == ep && r.event == event &&
           (event != 6 || r.presses == presses);
}
bool resolve(const Rule &r, State state, Command &cmd) {
    if (is_cover(r.op)) {
        if (!valid_operand(r.op, r.value)) return false;
        // HA counts percent open; Matter lift percentages count towards closed.
        uint16_t lift = (r.op == kCoverPosition || r.op == kTiltPosition) ? (100 - r.value) * 100 : 0;
        cmd = {r.op, lift};
        return true;
    }
    int brightness = r.value;
    if (r.op < 2) { cmd = {r.op, 0}; return true; }
    if (r.op == 2) {
        if (state.on != 0 && state.on != 1) return false;
        cmd = {static_cast<uint8_t>(!state.on), 0};
        return true;
    }
    if (r.op == 4 || r.op == 5) {
        if (state.on < 0 || (state.on && (state.level < 0 || state.level > 254))) return false;
        const int range = r.max_level - r.min_level;
        int previous = state.on ? rounded((std::clamp(state.level, int(r.min_level), int(r.max_level)) - r.min_level) * 255, range) : 0;
        if (r.op == 5) {
            const int pct = rounded(previous * 100, 255);
            brightness = int(std::nearbyint((pct + r.value) / 100.0 * 255));
        } else {
            brightness += previous;
        }
        brightness = std::clamp(brightness, 0, 255);
    }
    if (!brightness) { cmd = {0, 0}; return true; }
    cmd = {3, static_cast<uint8_t>(rounded(r.min_level * 255 + brightness * (r.max_level - r.min_level), 255))};
    return true;
}
}  // namespace reflex
