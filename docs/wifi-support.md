# Choose the networks for a XIAO ESP32-C6

For a fresh XIAO, build [the network package](../examples/xiao-networks.yaml) from the same checkout as the Home Assistant integration. Create a device YAML beside it in `examples/`. Supply only the network credentials this board should use. The API key secures rule transfer in all three configurations.

```yaml
packages:
  reflex: !include xiao-networks.yaml

esphome:
  name: hallway-reflex

substitutions:
  reflex_api_key: !secret reflex_api_key
  reflex_wifi_ssid: !secret wifi_ssid
  reflex_wifi_password: !secret wifi_password
  reflex_thread_tlv: !secret thread_tlv
```

| Network | Lines to include after the API key | Connection |
| --- | --- | --- |
| Thread | `reflex_thread_tlv` | Joins the supplied mesh. A border router provides access to the LAN. |
| Wi-Fi | `reflex_wifi_ssid` and `reflex_wifi_password` | Joins the LAN. An available border router provides access to Thread peers. |
| Combined | All three | Joins the LAN and the existing Thread dataset and starts ESPHome's built-in border router on the XIAO. |

Store the referenced values in `examples/secrets.yaml`. Obtain the active Thread dataset from the network your devices already use. ESPHome selects the interfaces at build time from the substitutions referenced by this node. All three profiles use the same two app slots on the 4 MB XIAO. Changing the selection requires installing a new build. For a commissioned board previously running standalone firmware, use the existing migration procedure before considering changes to its partition layout. [Obtaining the active dataset](../firmware/README.md#join-the-thread-network) · [Commissioned-board migration](../examples/README.md#upgrade-a-commissioned-standalone-board)

Build the device from the repository root with `./dev esphome compile examples/hallway.yaml` (if you named the device file `hallway.yaml`). Flash the fresh board over USB. Read its onboarding code from the ESPHome log and commission it on-network onto the same Home Assistant Matter fabric as its sources and targets. Add the encrypted ESPHome device to HA and select it if Reflex asks for a rules channel. The board handles direct and fallback rules over its Matter sessions in every mode. [On-network commissioning](../firmware/README.md#pair-with-home-assistant)

## Wi-Fi-only hardware test

On 24 November 2026, a commissioned XIAO ESP32-C6 running the Wi-Fi-only profile subscribed to an IKEA BILRESA button and a KAJPLATS lamp on Thread through a Google TV Streamer border router. With Home Assistant Core stopped and Matter Server still running, long presses on buttons 1 and 2 turned the desk lamp on and off. Each desk-lamp rule returned Matter success exactly once. Across six distinct input events, the board reported nine successful rule results. The tester observed all six automations work and perceived slower responses than with Thread-only Reflex or Home Assistant. The test did not measure button-to-actuator latency.

The tester also observed the Streamer reboot during initial setup. Home Assistant recorded its Cast entity becoming unavailable four seconds after the XIAO joined Wi-Fi. The cause is unknown. The lamp test ran after playback and both bureau-device subscriptions recovered.

| Measurement from this run | Time |
| --- | ---: |
| Time to subscribe after boot, desk lamp | 72.2 s |
| Time to subscribe after boot, BILRESA | 84.4 s |
| First to last logged command result for the lamp-on gesture (two rules) | 611 ms |
| First to last logged command result for the lamp-off gesture (three rules) | 320 ms |

The lamp result was logged first for the on gesture and last for the off gesture. The firmware logged each result on the ESPHome loop after the Matter response callback. Measuring the delay a person feels requires a timestamp for the press and an observation of the lamp changing. For a repeat comparison, use the same gesture, devices and placement for Thread-only Reflex, Wi-Fi-only Reflex and HA. The tested Wi-Fi firmware used ESPHome's `LIGHT` power saving. A second Wi-Fi run with `power_save_mode: none` can isolate that setting.

## Live round trips to the Thread desk lamp

On 25 November 2026, HA reached the same physical KAJPLATS lamp with IPv6 echo and read-only Matter requests. The probes only read the lamp state. The two echo series ran over the same interval from the HA SSH app. IPv6 traceroute identified the last responding router at hop two, after the app’s gateway. HA’s Thread discovery did not identify that responding address by model.

| Round trip | Replies | Median | 95th percentile | Maximum |
| --- | ---: | ---: | ---: | ---: |
| IPv6 echo to the last responding router | 24 | 2.1 ms | 8.3 ms | 9.5 ms |
| IPv6 echo to the Thread desk lamp | 24 | 75.0 ms | 297.7 ms | 366.4 ms |
| WebSocket ping to Matter Server | 20 | 3.0 ms | 4.2 ms | 12.6 ms |
| Matter read of the desk lamp’s On/Off attribute | 20 | 97.5 ms | 545.2 ms | 561.8 ms |

Matter Server 1.5.5 used its live attribute-read API on one established WebSocket connection. Each read returned the lamp’s On/Off value. A WebSocket ping before each read measured the client-to-server round trip. Both echo targets answered 24 of 24 probes, and all 20 reads succeeded. Percentiles use the nearest rank. [Raw round trips](../tests/lab/results/bureau-thread-path.csv)

These measurements cover the path from HA to the lamp. The XIAO and BILRESA were outside the timed exchange. Router forwarding, Thread radio delivery and lamp processing all contribute to a lamp round trip. Disentangling them requires timestamps at intermediate points. The earlier 611 ms and 320 ms figures measured time between consecutive rule results for one gesture.

## Wi-Fi power saving in the emulator lab

On 25 November 2026, a Wi-Fi-only ESPHome Reflex on ESP-EMU 1.2.0 ran a direct rule from a virtual Matter button to a virtual lamp on the same LAN. The button timestamped its completed double-press event and the lamp timestamped receipt of the On command using the same host monotonic clock. Every trial also checked the authenticated sender, the number of commands and the final lamp state.

| Wi-Fi power saving | Measured presses | Median | 95th percentile | Range |
| --- | ---: | ---: | ---: | ---: |
| LIGHT | 20 | 69.6 ms | 72.1 ms | 66.1–72.5 ms |
| NONE | 20 | 68.9 ms | 73.1 ms | 63.1–73.4 ms |

The Wi-Fi driver accepted and reported each requested mode on one boot with established subscriptions. The four blocks ran in LIGHT, NONE, NONE, LIGHT order, with one warm-up and ten measured presses per block. All 44 presses delivered exactly one On command from Reflex. The 95th percentile uses the nearest rank. The 0.7 ms median difference is smaller than the variation within either mode. The earlier BILRESA test also involved a physical access point, a Thread border router and a real lamp. The lab did not measure that route or verify how closely the virtual AP reproduces buffering for a sleeping station. [Raw samples](../tests/lab/results/wifi-power-save.csv)

To repeat the measurement on this branch, apply the [lab instrumentation patch](../tests/lab/results/wifi-power-save.patch) in a disposable checkout and run `REFLEX_LAB_WIFI_PS_BENCH=1 ./dev lab takeover --transport wifi --mode direct`.

## Losing the HA host and its border router

The combined configuration is for a home where loss of the HA host can also remove its Thread border router. Keep the AP, Reflex board and targets powered independently. With the same Thread credentials already provisioned, the XIAO can provide routing and discovery for the surviving mesh. A sleepy button may need to find a new parent before a fresh press can act. The ten-second lease measures HA reachability. Mesh recovery and peer subscriptions can take longer. The C6 time-shares its antenna between Wi-Fi and Thread, so physical range and interference matter. [ESPHome border-router mode](https://esphome.io/components/openthread/#openthread-border-router) · [Espressif coexistence guidance](https://docs.espressif.com/projects/esp-idf/en/stable/esp32c6/api-guides/coexist.html)

The combined package allows 96 mDNS services across the board’s own advertisements and the Thread services it publishes on behalf of peers. A single device can advertise multiple services. Available heap and radio conditions also affect the size of mesh a particular board can handle.

The combined lab has a separate emulated HA-host router. In the fallback test, OpenThread reported that the input changed parents after the HA-host router lost power. A fresh contact opening then turned the LAN lamp on and a long press turned it off. The lamp recorded Reflex as the sender of both commands, including after Reflex cold-booted with HA and its original router still offline. The standard input keeps its receiver on. The sleepy input detached in ESP-EMU 1.2.0, so battery-button handover still needs a hardware test. [Lab scenarios and verification](../tests/lab/README.md#network-profiles)
