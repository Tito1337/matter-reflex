# Lease traffic and failover on Thread

On 19 November 2026, one Seeed Studio XIAO ESP32-C6 running firmware 1.3.0 and the Home Assistant integration 0.5.1 exchanged **48.6 IPv6 packets per minute** with Home Assistant (HA). The XIAO stayed in standby through one missed renewal and took over after ten seconds of silence.

## Traffic measurements

Each column is a separate five-minute capture on the same installation. Rates count packets in both directions on the HA virtual machine's IoT interface between its IPv6 address and the Thread prefix. The XIAO is a subset of that traffic. All three captures reported zero kernel drops.

| Mean packets/minute | Timed on/off, 2.5 s | Named action, 2.5 s | Named action, fixed 4 s |
| --- | ---: | ---: | ---: |
| HA ↔ XIAO | 1,814.6 | 72.6 | 48.6 |
| HA ↔ other addresses in the Thread prefix | 55.2 | 47.4 | 51.0 |
| Total observed | 1,869.8 | 120.0 | 99.6 |
| Integration / firmware | 0.3.0 / 1.1.0 | 0.5.0 / 1.3.0 | 0.5.1 / 1.3.0 |

The first configuration used `OnWithTimedOff`, with a reportable countdown and frequent status reads. The next two used the standard Matter `Actions.InstantAction` to renew a private deadline. The change from 2.5 to 4 seconds alone reduced HA–XIAO traffic by **33.1%**. The four-second schedule uses a monotonic clock so normal response latency doesn't accumulate between sends.

In the four-second capture, Matter Server logged **75 requests and 75 successful responses**. The median interval between sends was **4.000 s** (range **3.997–4.003 s**). The capture counted 75 HA-to-XIAO UDP datagrams with a 78-byte Matter payload, consistent with one datagram per renewal. Replies, acknowledgments at the IP capture point, and other traffic to or from the board are included in the XIAO row.

The XIAO accounted for 48.8% of the packets observed at HA in the last window. Traffic that stays inside the mesh, per-hop radio frames, link-layer retries and airtime fall outside this capture. A UDP datagram can span multiple radio frames. Battery consumption was not measured.

## Failover on the four-second schedule

Only the Reflex integration was paused and restored. Matter Server remained online. We compared its successful command timestamps with the XIAO's `active` flag sampled over USB about every 200 ms. The board starts its ten-second deadline when it accepts a renewal. The controller's acknowledgment provides an observable reference close to that instant.

| Test | Observed behavior |
| --- | --- |
| Omit one scheduled renewal | Successful acknowledgments were **7.866 s apart**. All 39 USB samples taken in that gap showed standby. |
| Continue the pause beyond the deadline | The board was still in standby **9.912 s** after the last acknowledgment and was active at **10.113 s**. |
| Resume renewals | The board reported standby **34 ms** after the next successful acknowledgment. Home Assistant subsequently reported synchronized rules. |

A separate Core restart during the preceding upgrade also produced an active-to-standby recovery. The timing tests above exercised the lease state directly. The earlier physical button, light, plug and shade tests are recorded in the [hardware tests](../README.md#hardware-tests).

## Repeating the measurement

1. Let the board rejoin Thread and wait for **Synchronized** to turn on and **Active** to turn off.
2. On a single HA-facing interface, count IPv6 packets between HA and the Thread prefix for five minutes. Count packets to or from the XIAO separately. Keep the same interface, addresses, window length and direction filters across runs. Record capture drops.
3. For that window, count `InstantAction` invocations and successful responses for the XIAO in Matter Server logs. Use consecutive send timestamps to measure the schedule.
4. Outside the baseline window, pause just the Reflex integration while observing board status over USB. Use the command timestamps to verify the actual gap. Resume before expiry for the single-miss test, then repeat with a pause longer than ten seconds. Check standby and rule synchronization after restoring the integration.
