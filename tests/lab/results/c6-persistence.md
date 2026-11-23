# ESP32-C6 recovery from persistent memory

On November 22, 2026, the ESP-EMU lab restarted a commissioned Reflex board from saved flash with Matter Server offline. The border router, button publisher and lamp stayed powered. The source and target were independent Matter.js processes. The lamp recorded the authenticated sender and every On invocation, including repeated commands that left it on. The modified SDK and firmware ran only in the disposable lab build.

## Time to an authenticated lamp command

On a second fabric the runner interleaved five boots per variant. It began one double press as soon as it observed both CASE callbacks and timestamped the authenticated On inside the lamp handler. The host and both Podman fixtures use the same Linux monotonic clock. No artificial delay was added to either subscription. Each reported time includes emulator startup, the wait for both CASE callbacks, the real gesture timer and delivery over the virtual mesh.

| Direct rule on a cold boot | First press delivered | Power-on to lamp receipt for successful first presses, median (range) |
| --- | ---: | ---: |
| Cached addresses, both subscriptions fresh | 5/5 | 4.523 s (4.375–4.784) |
| Restore the source subscription, wait for target subscription | 3/5 | 3.919 s (2.930–5.735) |
| Restore the source subscription, allow an absolute On over target CASE | 5/5 | 3.955 s (3.061–5.278) |

The combined strategy improved the sample median by 0.568 s. Its slowest run took 0.494 s longer than the slowest fresh-subscription run. With five trials per variant the result supports testing the combined strategy on the physical button and lamp. A hardware latency bound needs more trials.

Of the two first-press misses in the source-only group, one reached the ESP before its target subscription completed. The ESP sent zero commands to the lamp in that trial. The original harness then incorrectly waited for a new source subscription even though that source had delivered. The raw record retains the timeout. The other trial recovered via a fresh subscription, and a second press reached the lamp 15.706 s after power-on. With the corrected observer, a third fabric delivered 2/3 first presses with source restoration alone. On the miss, the second press succeeded on the same subscription at 8.872 s from power-on. Across the two fabrics this variant delivered 5/8 first presses.

The [follow-up early On study](c6-early-on.md) tests the application-level gate independently with ordinary subscriptions and an unmodified Matter SDK.

## What the board can restore

| Experiment | Observed result |
| --- | --- |
| Restore the Thread locator | With the saved locator, three child-role boots attached in 0.237, 0.667 and 0.667 s. With the locator withheld, three child-role boots attached in 1.557, 0.797 and 0.667 s. The control kept the Thread dataset, identity and security counters. A separate saved-router boot attached in 1.747 s. |
| Use the cached Matter addresses | The same three cached child-role boots received both subscription confirmations at 1.477, 2.077 and 1.887 s. With endpoint lookup disabled and discovery enabled, two child-role boots took 7.757 and 7.597 s. Both paths used authenticated CASE. |
| Restore the subscriber on a new CASE session | Two brief interruptions and one 5.319 s interruption each delivered exactly one fresh double press through the old button subscription. All three traces showed zero outbound button SubscribeRequests and one authenticated lamp On. The target subscribed normally. |
| Press while the ESP is off | A press during the interruption produced no lamp command. In this trial the first post-boot press on the restored listener also produced none. The ten-second experimental timeout triggered a fresh subscription, and the next press worked. Both subscriptions were confirmed at 14.657 s on the guest clock. |
| Hold the publisher unavailable for eight seconds | Pausing the button process delayed its CASE handshake while the target reconnected. Both the fresh-subscription and restored-subscription trials delivered the press issued on wake and the subsequent press. This models a temporarily unavailable publisher. The real Aqara's radio polling and battery firmware require a physical test. |
| Send an absolute On before the target subscription completes | In four boots the fixture held the target's outgoing SubscribeRequest for six guest seconds after authenticated CASE. Under the existing gate the lamp received 0/2 early presses. Allowing direct absolute On over the live session delivered 2/2. Every later press worked. Toggle and relative commands still require fresh state. |

When the test explicitly closed the publisher’s subscription while the ESP was off, loading the saved ID produced no lamp command for the first fresh press. The experimental ten-second listener timeout fell back to a normal SubscribeRequest. Both confirmations arrived at 14.167 s guest time, and the next press worked. An idle boot also exercised fallback with zero unsolicited lamp commands. The timeout was a test choice, distinct from the HA lease. A saved ID alone provides insufficient evidence to mark an automation ready for HA handoff.

The network timings in the table above use the ESP's serial clock. Short samples, Thread role changes, and the two-node setup limit extrapolation to an eight-peer installation. A saved location helps the device reconnect to a live neighbor. The board still exchanges radio messages after reset.

## Flash writes and replay

No cold boot in this study logged a peer-address write for an unchanged endpoint. Each of the four cached control boots wrote one new source subscription record. The three successful short-cut restoration probes reused the saved ID without rewriting that record.

Seven actionable presses with per-event persistence yielded seven commit records, and the next boot recovered the seventh event number, 236. In the batch-of-four trial the ESP handled seven more presses and committed only event 260. It rebooted still remembering 260 after executing events 266, 272 and 278. All seven corresponding lamp commands arrived in each trial. The older cursor leaves those last three actions unaccounted for in flash. The normal fresh subscription ignored historical priming events in the subsequent boot.

| Policy for 2,048 monotonically increasing 64-bit event numbers | NVS commits | Successful partition writes | 4 KiB sectors erased |
| --- | ---: | ---: | ---: |
| Every actionable event | 2,048 | 2,048 | 15 |
| Every fourth event | 512 | 512 | 4 |

These counters cover the benchmark task's operations on the NVS partition, including its garbage collection. Other SDK traffic and study-mode selection writes are excluded. Initial page occupancy affects the exact erase count. This tests write amplification on an emulated partition. Physical flash lifetime depends on the part, the other NVS users and the real event rate. Recording an event before dispatch also leaves a power-failure window between committing it and sending the command.

## Reproduction

The [experiment patch](c6-persistence.patch) applies in a separate clean checkout at `5bd5cb2`. It includes the preceding cold-boot changes, the test-only firmware and SDK probes, and the harness. The normal development firmware uses the original subscriber. In the isolated checkout, apply the patch with `git apply /absolute/path/to/c6-persistence.patch`, then run the series below with the usual local lab dependencies.

```sh
REFLEX_PERSISTENCE_STUDY=1 ./dev lab takeover --mode direct
REFLEX_PERSISTENCE_STUDY=1 REFLEX_STUDY_ONLY=timed ./dev lab takeover --mode direct
REFLEX_PERSISTENCE_STUDY=1 REFLEX_STUDY_ONLY=recheck ./dev lab takeover --mode direct
```

Each series appends to `observations.jsonl` in that checkout. The [recorded observations](c6-persistence.jsonl) include the local node and subscription IDs from disposable fabrics, firmware milestones, event numbers, command counts and erase counts. ESP-EMU saves flash on a clean emulator interrupt and starts a new process with fresh RAM. These runs exercise completed flash writes and cold starts. Interruption during an individual flash operation, production HA handoff, and recovery with the whole mesh powered down are outside this experiment.

