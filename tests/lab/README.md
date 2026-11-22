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

On Linux x86-64 or AArch64, run the same command as the firmware workflow:

```sh
./dev lab takeover
```

The first run builds the pinned HA test image, downloads a checksum-pinned ESP-EMU release, builds this checkout’s ESP32-C6 firmware and builds ESP-IDF’s OpenThread border router example. Later runs reuse the builds under `firmware/build`. The host needs Python 3, rootless Podman with IPv6 networking, and a usable `/dev/net/tun`. The TAP and IPv6 route settings belong to a disposable container network namespace. Each run generates fresh factory data, fabric credentials and a Home Assistant instance. No board, USB forwarding, household credentials or live HA instance is required.

Two emulator processes exchange 802.15.4 frames. The runner provisions the unmodified Reflex firmware through its USB console, waits for routed IPv6 and commissions it with the button and targets. The installed HA integration renews the lease over Matter. When the pinned Matter Server requests the documented HTTP fallback for rule transfers, the runner provisions a fresh key over the virtual USB console and completes the normal HA setup flow. The button, targets and controller run in independent processes. The test stops the Matter Server container, keeps the virtual Thread network alive and drives both gestures and all five sensor changes in each phase.

| Phase | Required sender at every actuator | Other evidence |
| --- | --- | --- |
| Healthy HA | HA controller | Installed rules, standby lease, board subscriptions |
| Matter Server stopped | Emulated Reflex node | Lease expired, board execution count increases by seven |
| Controller restarted | Original HA controller | New controller subscriptions, renewed lease, board count holds |

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
