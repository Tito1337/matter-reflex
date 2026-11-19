#pragma once

#include <cstdint>

namespace reflex {

// All times are monotonic microseconds from the Matter event loop.
class LeaseDeadline {
public:
    static constexpr uint16_t kQuietSeconds = 10;

    void reset(int64_t now) {
        end_us_ = now + kQuietSeconds * kSecond;
        granted_seconds_ = 0;
    }

    bool renew_tenths(int64_t now, uint16_t tenths) {
        if (tenths == 0 || tenths > 600) return false;
        end_us_ = now + tenths * (kSecond / 10);
        granted_seconds_ = (tenths + 9) / 10;
        return true;
    }

    bool expired(int64_t now) const { return now >= end_us_; }
    uint16_t granted_seconds() const { return granted_seconds_; }

private:
    static constexpr int64_t kSecond = 1000000;
    int64_t end_us_ = 0;
    uint16_t granted_seconds_ = 0;
};

} // namespace reflex
