#include "reflex_engine.h"
#include <cassert>
using namespace reflex;
int main() {
    uint8_t wire[] = {0x4d,0x52,0x46,0x58,1,1,0,0,0,0,0,1,0x12,0x34,0x56,0x78,
                      0x9a,0xbc,0xde,0xf0,0,2,6,2,0,0,0,0,0,0,0,42,0,1,3,0,178,1,254,0};
    Rules rules{};
    assert(decode(wire, sizeof(wire), rules));
    auto r = rules.rows[0];
    assert(matches(r, 0x123456789abcdef0ULL, 2, 6, 2));
    assert(!matches(r, r.source, 2, 6, 1));
    Command c{};
    assert(resolve(r, {-1,-1}, c) && c.op == 3 && c.value == 178);
    r.op = 2;
    assert(!resolve(r, {-1,-1}, c));
    assert(resolve(r, {1,-1}, c) && c.op == 0);
    r.op = 4; r.value = -80;
    assert(resolve(r, {1,42}, c) && c.op == 0);
    r.op = 5; r.value = 10;
    assert(resolve(r, {0,-1}, c) && c.op == 3 && c.value == 27);
    wire[39] = 1;
    assert(!decode(wire, sizeof(wire), rules) && rules.count == 1 && rules.rows[0].value == 178);
    wire[39] = 0;

    // The original MRFX v1 record size and layout also carry covering actions.
    auto decode_action = [&](uint8_t op, int16_t argument) {
        wire[34] = op;
        wire[35] = static_cast<uint16_t>(argument) >> 8;
        wire[36] = static_cast<uint8_t>(argument);
        return decode(wire, sizeof(wire), rules);
    };
    // A basic outlet reports OnOff but never CurrentLevel.
    for (uint8_t op = 0; op <= 1; ++op) {
        assert(decode_action(op, 0));
        assert(resolve(rules.rows[0], {-1, -1}, c) && c.op == op);
    }
    assert(decode_action(2, 0));
    assert(!resolve(rules.rows[0], {-1, -1}, c));
    assert(resolve(rules.rows[0], {0, -1}, c) && c.op == 1);
    assert(resolve(rules.rows[0], {1, -1}, c) && c.op == 0);

    for (uint8_t op = kCoverOpen; op <= kCoverStop; ++op) {
        assert(decode_action(op, 0));
        assert(resolve(rules.rows[0], {-1,-1}, c) && c.op == op && c.value == 0);
        assert(!decode_action(op, 1));
        assert(!decode_action(op, -1));
    }
    for (int position = 0; position <= 100; ++position) {
        assert(decode_action(kCoverPosition, position));
        assert(rules.rows[0].value == position);
        assert(resolve(rules.rows[0], {-1,-1}, c));
        assert(c.op == kCoverPosition && c.value == (100 - position) * 100);
    }
    assert(!decode_action(kCoverPosition, -1));
    assert(!decode_action(kCoverPosition, 101));
    assert(!decode_action(10, 0));
    assert(rules.rows[0].op == kCoverPosition && rules.rows[0].value == 100);
}
