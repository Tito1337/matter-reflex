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
    case kCoverOpen: case kCoverClose: case kCoverStop: return value == 0;
    case 3: return value >= 0 && value <= 255;
    case 4: return value >= -255 && value <= 255;
    case 5: return value >= -100 && value <= 100;
    case kCoverPosition: return value >= 0 && value <= 100;
    default: return false;
    }
}
int rounded(int num, int den) {
    const int q = num / den, r = num % den;
    return q + ((2 * r > den) || (2 * r == den && (q & 1)));
}
}
bool decode(const uint8_t *data, size_t len, Rules &out) {
    if (!data || len < 8 || std::memcmp(data, "MRFX", 4) || data[4] != 1 ||
        data[5] > kMaxRules || data[6] || data[7] || len != 8u + 32u * data[5]) return false;
    Rules next{};
    for (size_t i = 0; i < data[5]; ++i) {
        const uint8_t *p = data + 8 + 32 * i;
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
        if (!r.id || !operational(r.source) || !operational(r.target) ||
            !r.source_ep || r.source_ep == 0xffff || !r.target_ep || r.target_ep == 0xffff ||
            r.event > 6 || r.event == 5 ||
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
        uint16_t lift = r.op == kCoverPosition ? (100 - r.value) * 100 : 0;
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
