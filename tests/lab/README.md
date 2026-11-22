# Matter outage lab

The lab runs two independent matter.js nodes. One exposes a momentary button. The other exposes a lamp on endpoint 1 and a tilt-capable blind on endpoint 2. The test operates the button through a container-local Unix socket. It observes incoming commands in the target's Matter handlers, including the sending session's node ID. Button input and target observation both work with Matter Server offline.

## Run without a board

From the repository root on Linux with rootless Podman, run the protocol check. It commissions both peers on an in-memory network, subscribes to the actual switch events and checks authenticated command receipts, including two identical lamp commands.

```sh
./dev lab smoke
```

For a full Home Assistant check, install Python 3 on the host and build the project's pinned HA test image. On NixOS, `nix-shell -p python3` supplies the host interpreter. The command starts a fresh HA instance, Matter Server and both peers on an internal IPv6 Podman network. It commissions through HA, discovers the three entities, writes and labels the two automations, operates the button and checks the commands sent by HA. It then stops Matter Server, verifies that both peers remain available and quiet, restarts the server and repeats the gestures. It cleans up its containers and network when it exits. Podman needs IPv6 bridge networking for this check.

```sh
podman --cgroup-manager=cgroupfs build -f tools/Containerfile.ha -t localhost/matter-reflex-ha:2026.11.1 tools
./dev lab smoke-ha
```

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
