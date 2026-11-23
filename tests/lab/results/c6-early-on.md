# Early absolute On after an ESP32-C6 cold boot

A direct button rule can send an absolute On in the tested window once the board has received a fresh, matching button event and holds an active authenticated session to the lamp. In six boots where the gesture landed before the lamp subscription finished, the current gate dropped all three first presses and the early gate delivered all three exactly once. The ordinary runs showed no typical boot speedup attributable to the gate.

| First double press | Current gate | Early On |
| --- | ---: | ---: |
| Immediately after both secure session callbacks, ordinary subscriptions | 1/12 reached lamp | 0/12 reached lamp |
| Immediately after fresh button subscription, ordinary lamp, first press received | 8/8 | 8/8 |
| Same runs, median wall time from power-on to authenticated lamp receipt (range) | 6.947 s (5.323–8.356) | 7.028 s (5.314–8.143) |
| Lamp subscription deliberately delayed, still pending at dispatch | 0/3 reached lamp | 3/3 reached lamp |
| Lamp subscription deliberately delayed, already confirmed at dispatch | 4/4 reached lamp | 4/4 reached lamp |

The first series triggered the gesture as soon as both CASE callbacks appeared. On the 23 misses, the board logged no matching button action. The lamp subscription finished ahead of the button subscription on 23 of those 24 boots. The one time the button was ready first, the 260 ms window closed before the gesture completed. The button subscription took a median of 6.022 s on the guest clock, and the lamp subscription took 3.217 s. Every follow-up press in this series worked. In the button-ready series the lamp subscription had already completed at dispatch on all 16 boots. This distinguishes the timing variation in the table from the benefit of bypassing the lamp gate.

The three successful genuinely early commands reached the lamp 4.279, 5.141 and 5.608 s after power-on. The firmware dispatched them 3.630–4.370 guest seconds before the lamp subscription completed. The ordinary button subscription and the active lamp CASE session were present each time. The 6 s delay was injected before sending the lamp SubscribeRequest and started at lamp CASE establishment.

## Other commands and presses during a cut

Two long presses arrived while the target subscription was pending. The board matched the cover tilt rule, issued no command, and handled the next double press after the subscription completed. Two more long presses arrived after the subscription was ready and correctly moved the cover. The original runner expected those presses to be early and flagged the two legitimate tilt commands as errors. The per-boot records retain those assertions and the separately captured firmware timestamps. The second runner checked the actual dispatch state.

On two other boots the button was double-pressed while the board was off. Neither boot replayed that gesture, and the first fresh double press after both subscriptions completed reached the lamp exactly once. None of the 60 trials recorded a duplicate first command. All 58 follow-up checks that ran passed. The two original cover assertions ended their trials before the follow-up.

## Setup and reproduction

The runner cold-booted a commissioned ESP-EMU ESP32-C6 on two disposable fabrics, with Matter Server offline and the Thread border router, button and lamp powered. It alternated which variant ran first across pairs. Both variants used the same firmware image and ordinary fresh Matter subscriptions. Cached peer addresses and route-aware startup were enabled for both. The only change in dispatch allowed an absolute On from a direct button rule to use its active target session while its target subscription was pending. Both variants kept the normal ownership and button-event handling. No SDK modification or event-cursor persistence was used.

Power-on begins when the host releases the emulator's power-off marker. The Matter.js lamp timestamps each first authenticated On inside its handler using the host monotonic clock. Those wall times include emulator startup, starting the gesture and transmitting it over the virtual mesh. Firmware milestones use a separate guest clock. These are two-peer emulator measurements. The 30+ s delay on the physical Aqara installation needs a trace from that installation.

The [per-boot observations](c6-early-on.jsonl) include the firmware state when the rule matched. The [lab-only reproduction patch](c6-early-on.patch) applies to a separate clean checkout at `27aea2e` and includes the route-aware baseline. With the normal takeover lab dependencies, run both series from that checkout.

```sh
git apply /path/to/c6-early-on.patch
REFLEX_EARLY_ON_STUDY=1 ./dev lab takeover --mode direct
REFLEX_EARLY_ON_STUDY=1 REFLEX_EARLY_SERIES=source ./dev lab takeover --mode direct
```

The runner appends to `early-results.jsonl`. The reproduction patch includes the corrected cover assertion for both series.
