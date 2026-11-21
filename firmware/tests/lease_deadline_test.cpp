#include "reflex_lease.h"

#include <cassert>

int main() {
    reflex::LeaseDeadline lease;
    constexpr int64_t now = 5'000'000'000; // Exercise the 64-bit monotonic clock.
    lease.reset(now);
    assert(lease.granted_seconds() == 0);
    assert(!lease.expired(now + 9'999'999));
    assert(lease.expired(now + 10'000'000));

    constexpr uint16_t quiet_tenths = reflex::LeaseDeadline::kQuietSeconds * 10;
    assert(lease.renew_tenths(now, quiet_tenths));
    assert(lease.granted_seconds() == 10);
    assert(lease.renew_tenths(now + 2'500'000, quiet_tenths));
    assert(!lease.expired(now + 12'499'999));
    assert(lease.expired(now + 12'500'000));
    assert(lease.granted_seconds() == 10); // Stable grant, even after expiry.

    assert(lease.renew_tenths(now, 15)); // Legacy tenths retain their exact duration.
    assert(lease.granted_seconds() == 2);
    assert(!lease.renew_tenths(now, 601));
    assert(!lease.renew_tenths(now, 0));
    assert(!lease.expired(now + 1'499'999));
    assert(lease.expired(now + 1'500'000));
    assert(lease.renew_tenths(now, 600));
    assert(lease.granted_seconds() == 60);
    lease.reset(now);
    assert(lease.granted_seconds() == 0);
    assert(lease.expired(now + 10'000'000));
}
