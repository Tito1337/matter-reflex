#pragma once
#include <cstddef>
#include <cstdint>

namespace reflex {
constexpr size_t kMaxRules = 32;
constexpr size_t kMaxImage = 8 + 36 * kMaxRules;
struct Rule {
    uint32_t id;
    uint64_t source, target;
    uint16_t source_ep, target_ep;
    uint8_t event, presses, op;
    int16_t value;
    uint8_t min_level, max_level;
    int16_t threshold;
    uint16_t transition;
};
struct Rules { Rule rows[kMaxRules]; size_t count; };
struct State { int on, level; };  // -1 when unavailable
enum CoverOperation : uint8_t { kCoverOpen = 6, kCoverClose, kCoverStop, kCoverPosition, kTiltPosition, kTiltOpen, kTiltClose, kTiltStop };
constexpr bool is_cover(uint8_t op) { return op >= kCoverOpen && op <= kTiltStop; }
enum SensorEvent : uint8_t {
    kBooleanFalse = 7, kBooleanTrue, kUnoccupied, kOccupied,
    kTemperatureAbove, kTemperatureBelow, kHumidityAbove, kHumidityBelow
};
uint32_t sensor_cluster(uint8_t event);
// A missing or invalid report discards the baseline. A new subscription starts unknown.
struct SensorState {
    int8_t previous = -1;
    bool report(const Rule &rule, uint64_t node, uint16_t ep, uint32_t cluster, int32_t value);
};
constexpr int32_t kInvalidMeasurement = INT32_MIN;
// Argument is a native LevelControl level or WindowCovering lift percent100ths.
struct Command { uint8_t op; uint16_t value; };
bool decode(const uint8_t *bytes, size_t len, Rules &out);
bool matches(const Rule &rule, uint64_t source, uint16_t ep, uint8_t event, uint8_t presses);
bool resolve(const Rule &rule, State state, Command &command);
}  // namespace reflex
