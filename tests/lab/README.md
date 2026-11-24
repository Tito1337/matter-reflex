# Matter outage lab

The lab runs two independent matter.js nodes. One publishes inputs and the other receives actuator commands. The original scenario pairs a momentary button with a lamp and a tilt blind. The disposable HA runs also include five sensors, a plug, a dimmer and a lift shade. The test operates the button through a container-local Unix socket. It observes incoming commands in the target's Matter handlers, including the sending session's node ID. Button input and target observation both work with Matter Server offline.

## Run without a board

From the repository root on Linux with rootless Podman, run the protocol check. It commissions both peers on an in-memory network, subscribes to the actual switch events and checks authenticated command receipts, including two identical lamp commands.

```sh
./dev lab smoke
```

For a full Home Assistant check, install Python 3 on the host and build the project's pinned HA test image. On NixOS, `nix-shell -p python3` supplies the host interpreter. The command starts a fresh HA instance, Matter Server and both peers on an internal IPv6 Podman network. It commissions through HA, discovers the fixture entities, installs seven labeled automations and checks HA’s commands at the actuators. It then stops Matter Server, verifies that both peers remain available and quiet, restarts the server and repeats the gestures and sensor changes. It cleans up its containers and network when it exits. Podman needs IPv6 bridge networking for this check.

```sh
podman --cgroup-manager=cgroupfs build -f tools/Containerfile.ha -t localhost/matter-reflex-ha:2026.11.1 tools
./dev lab smoke-ha
```

## Firmware takeover regression

Run the firmware regression locally on Linux x86-64 or AArch64. Select one mode for a focused check, or use `all` to prepare the firmware and border router once and run each mode on a fresh fabric. Running `./dev lab takeover` selects fallback.

```sh
./dev lab takeover --mode direct
./dev lab takeover --mode fallback
./dev lab takeover --mode advanced
./dev lab takeover --mode all
```

For a hosted run, select **Actions → ESP32-C6 firmware → Run workflow** and choose a mode. The menu defaults to direct. The full emulator runs only on request.

The first run builds the pinned HA test image, downloads a checksum-pinned ESP-EMU release, builds this checkout’s ESP32-C6 firmware and builds ESP-IDF’s OpenThread border router example. Later runs reuse the builds under `firmware/build`. The host needs Python 3, rootless Podman with IPv6 networking, and a usable `/dev/net/tun`. The TAP and IPv6 route settings belong to a disposable container network namespace. Each run generates fresh factory data, fabric credentials and a Home Assistant instance. No board, USB forwarding, household credentials or live HA instance is required.

Two emulator processes exchange 802.15.4 frames. The runner provisions the unmodified Reflex firmware through its USB console, waits for routed IPv6 and commissions it with the button and targets. The installed HA integration renews the lease over Matter. The disposable controller loads the Reflex vendor-cluster schema so the normal HA setup flow transfers rules over authenticated Matter sessions. The runner does not provision an HTTP key or open a management HTTP listener. The button, targets and controller run in independent processes. The test stops the Matter Server container, keeps the virtual Thread network alive and drives both gestures and all five sensor changes in each phase.

The targets check the authenticated Matter node ID against the commissioned ESP and HA controller. The board execution counter must increase by exactly the number of commands assigned to the ESP. After restarting Matter Server, the runner waits for fresh HA subscriptions and a renewed lease. All three scenarios also stop just the Reflex emulator, leave HA and the border router up, then restart the board from its saved flash and wait for fresh ESP subscriptions. Each recovery repeats the healthy expectations.

| Policy | Both online | Matter Server stopped | ESP stopped |
| --- | --- | --- | --- |
| Direct | ESP | ESP | HA |
| Fallback | HA | ESP | HA |
| Advanced, per rule below | Normal handler | Surviving handler or silence | Surviving handler or silence |

| Advanced rule | Both online | Matter Server stopped | ESP stopped |
| --- | --- | --- | --- |
| Either button gesture | ESP | ESP | HA |
| Contact | HA | ESP | HA |
| Occupancy | ESP | ESP | Silent |
| Leak | HA | Silent | HA |
| Temperature | Silent | Silent | HA |
| Humidity | Silent | ESP | Silent |

| Input | Expected actuator command |
| --- | --- |
| Button double press | Lamp on |
| Button long press | Blind tilt to 75% open (2500 on the wire) |
| Door opens (Matter contact changes from true to false) | Plug on |
| Occupancy detected | Dimmer to HA brightness 200 with a 0.2-second fade (Matter level 199, transition 2) |
| Water leak detected | Plug off |
| Temperature falls below 0 °C | Shade lift to 25% open (7500 on the wire) |
| Humidity rises above 55% | Shade lift to 80% open (2000 on the wire) |

The sensor test observes each command and the resulting actuator state before changing the next input. Further readings on the same side of a threshold must stay quiet. Returning the binary inputs to normal and the numeric inputs to the exact thresholds rearms the next phase.

Each phase checks the exact command count, order, arguments and actuator states. A missing command, wrong sender or duplicate fails the job. The radios and SoC peripherals are emulated. This regression covers firmware and Matter behavior under controller loss. RF range, interference and physical board peripherals require separate testing.

The upstream emulator documents the radio bridge and the IDF example used here. [ESP-EMU Thread guide](https://github.com/espressif/esp-emulator/blob/v1.2.0/docs/guides/THREAD.md)

## Measure recovery after ESP power loss

Run `./dev lab takeover --mode direct --cold-boots 7` to measure seven restarts after commissioning. The runner stops Matter Server, leaves the Thread border router and peers powered, and restarts the ESP emulator from its saved flash each time. Use `--mode fallback` to include the firmware's ten-second boot grace before a fallback rule can act.

Run `./dev lab takeover --mode direct --toggle-recovery --cold-boots 3` to check native Toggle with a late lamp subscription. This test uses `nft` and `nsenter` on the Linux host. Within the disposable TAP network namespace it temporarily drops encrypted replies from the lamp while allowing CASE establishment and button reports. On each boot it checks for replay of an offline press, sends two fresh matching gestures, and verifies two commands from the ESP and the lamp state after each. It then restores replies and checks subscription recovery and the final state. On November 23, 2026, all three cold boots delivered exactly two native Toggles from the ESP before the lamp subscription completed. The lamp went off → on → off on each boot.

The runner records two clocks. On the ESP, the serial trace timestamps the return from `app_main`, Thread attachment, the first outgoing CASE Sigma1, and the second distinct peer's SubscribeResponse. On the host, the runner times from virtual power-on until it observes fresh subscriptions on both peers, sends one new double press, and verifies the lamp's On command and the ESP sender ID. The host time is an upper bound that includes polling, gesture recognition, and receipt observation. The separate HA handback check waits eleven seconds to distinguish a renewed lease from the board's initial standby period.

On November 22, 2026, seven direct-mode boots with the 500 ms polling firmware at `5bd5cb2` were compared with seven boots that also wake on Thread role and Network Data changes. Each series used a fresh fabric with the same seven rules, one source node and one target node.

| From boot to milestone | Polling, median (range) | Thread events, median (range) |
| --- | --- | --- |
| ESP returned from `app_main` | 0.157 s (all runs) | 0.157 s (all runs) |
| ESP attached to Thread | 1.17 s (1.00–1.86) | 1.44 s (1.01–1.78) |
| ESP received both SubscribeResponses | 3.26 s (2.26–5.06) | 2.84 s (2.59–3.11) |
| Host verified a fresh lamp On from the ESP | 5.92 s (5.65–8.30) | 5.64 s (4.87–6.44) |

On three polling boots the ESP sent both cached CASE requests before OpenThread had a route to the peers. The route appeared shortly afterward, and the requests went through on Matter's retransmission about two seconds later. The modified firmware saw zero missing-route drops across seven boots. These are observations from the standalone C6 image with the border router and peers already online. Timing a whole-mesh power cut or a physical ESPHome boot requires a separate measurement.

In two additional fallback boots, the ESP received both subscription confirmations at 1.80 s and 2.25 s. With Matter Server still offline, the probe waited for the ESP to report an expired boot grace, then sent the gesture. The host verified the lamp command at 12.60 s and 12.84 s, respectively.

The [persistent-memory experiment](results/c6-persistence.md) compares Thread locator restoration, discovery, resuming the original button subscription, early absolute On commands, and event-checkpoint flash wear.

## Run against a test XIAO

Use a dedicated Home Assistant fabric with Matter Server running in a named local Podman container. Connect the XIAO to that fabric through its Thread border router and configure the Matter Reflex integration. Keep HA and the border router in processes independent of Matter Server. The test host interface must support multicast discovery and routed IPv6 between the Thread board and the virtual peers. The peers listen on UDP ports 5570 and 5571. Enable test certificates on the lab Matter Server (the pinned JS server accepts `--enable-test-net-dcl`). The HA instance needs editable automations, as in the default `automation: !include automations.yaml` configuration.

1. Start the two persistent peer containers on the test interface. Replace `eth0` with the interface on the lab LAN.

   ```sh
   ./dev lab up --interface eth0
   ```

2. Set `HA_TOKEN` in the environment to an admin token for the lab HA. Run setup against that instance. It commissions fresh peers, finds existing ones by serial number, installs or updates the two automations under IDs beginning with `matter_reflex_lab_`, and applies the `reflex` label. Repeating setup uses the same IDs. Home Assistant compiles the rules and grants peer access through the normal integration.

   ```sh
   ./dev lab setup --ha-url http://lab-ha:8123
   ```

3. Supply the XIAO's operational Matter node ID on that fabric (decimal, or hexadecimal prefixed with `0x`), the lab container name, and the entity IDs from the XIAO's HA device page. The runner queries both peers directly to verify they have a subscription from the board. Use `--controller-node` to pin the expected HA sender if known. Otherwise the runner observes the sender in the first phase and requires the same sender after recovery.

   ```sh
   ./dev lab run --ha-url http://lab-ha:8123 --server lab-matter-server \
     --reflex-node 0x2a --active-entity binary_sensor.lab_reflex_active \
     --synced-entity binary_sensor.lab_reflex_synchronized
   ```

The runner first waits for synchronized rules and standby. In each phase a double press must deliver one lamp On and a long press must deliver one blind tilt of 75% open (2500 hundredths closed on the wire). It checks order, count, authenticated sender and target state, then observes for late duplicate commands for four seconds by default. The healthy phase must come from HA. The runner stops the **named Matter Server container**, waits past the ten-second lease, and repeats with the XIAO as the required sender. It starts the server again even on assertion failure or interruption, waits for synchronization and standby, and repeats with HA as the required sender.

To repeat the recovery check after a board reboot, power-cycle the lab XIAO and run the same command. To remove the running peer containers, run `./dev lab down`. Their named data volumes retain commissioning for the next run. The two HA automations remain available for repeat tests.
