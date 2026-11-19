# ESPHome prototype for the XIAO ESP32-C6

The [board configuration](xiao-esp32c6.yaml) uses ESPHome 2026.11.0 and pins the community Matter component to a Git revision. ESPHome owns Thread and commissioning. The local `matter_reflex` component registers endpoints 1 and 2, runs the shared firmware engine, and receives the four-second lease renewals through Matter Server. Home Assistant still compiles the labeled automations and grants the board access to the peers.

## Build

1. Use a checkout with working Git symlinks. The external component links to the same C++ sources that the standalone firmware builds.
2. Create `examples/secrets.yaml` with two quoted strings. Set `thread_tlv` to the active operational dataset of the test mesh and `reflex_transfer_key` to a new 64-digit hexadecimal key. Home Assistant uses that key for the authenticated rule-transfer fallback. Both values are embedded in the compiled image. [Obtaining the Thread dataset](../firmware/README.md#join-the-thread-network)
3. From the repository root, build with the pinned ESPHome container and rootless Podman.

```sh
./dev esphome compile examples/xiao-esp32c6.yaml
```

## Test on a spare board

Use a spare XIAO or back up the full flash before switching a deployed board. The ESPHome example uses a different partition layout and commissioning data. With ESPHome 2026.11.0 installed on the USB host, choose the intended device explicitly.

```sh
esphome run examples/xiao-esp32c6.yaml --device /dev/serial/by-id/YOUR_SPARE_XIAO
```

1. Read the Matter onboarding code from the ESPHome USB log and commission the board on-network to Home Assistant. Add the Matter Reflex integration for that node and supply the transfer key if prompted. [On-network commissioning](../firmware/README.md#pair-with-home-assistant)
2. Use one labeled button-to-lamp automation on the test fabric. Confirm **Synchronized** is on and **Active** is off. Keep any other Reflex board from executing the same test rule.
3. Stop Matter Server while Home Assistant and the Thread border router stay up. Keep the server down, wait more than ten seconds, then double-press the button. Observe the physical lamp. Restore Matter Server and check that the executions count advanced once and the board returns to standby.
4. Power-cycle the XIAO. Confirm it rejoins, restores the same rules hash and resubscribes, then repeat the outage test. [Reference observations with the standalone firmware](../docs/lease-measurements.md)

## Upgrade a commissioned standalone board

The [existing-board profile](xiao-esp32c6-existing.yaml) uses the standalone firmware’s partition table. It loads the stored OpenThread dataset, fabric, rules and transfer key from NVS. Use this profile only on a board that has already joined the mesh with standalone Reflex. Select the board by its USB ID and take the full-flash backup described in the [firmware guide](../firmware/README.md#flash-the-board).

Build from the repository root. Compare the generated partition table against your backup before writing the application to the board.

```sh
./dev esphome compile examples/xiao-esp32c6-existing.yaml
build=examples/.esphome/build/matter-reflex/build
table="$build/partition_table/partition-table.bin"
cmp -n "$(wc -c < "$table")" -i 49152:0 /path/to/private/flash.bin "$table"
```

After the comparison succeeds, write only the existing factory application at offset `0x20000`. The board retains its bootloader and NVS.

```sh
./dev usb bash -ec 'python -m esptool --chip esp32c6 --port "$ESPPORT" write_flash 0x20000 /work/examples/.esphome/build/matter-reflex/build/matter-reflex.bin'
```

Check the original Reflex device in Home Assistant for synchronized rules and standby. The ESPHome USB log reports the node ID and rule fingerprint on startup and prints lease state changes.

## Hardware observations

On November 19, 2026, the application-only profile booted on an already commissioned XIAO ESP32-C6. It restored the same Matter node ID and rule-table SHA-256. The table contained 17 rules across eight peers.

With Home Assistant running, Matter Server was stopped for 32 seconds. The board’s USB log recorded takeover during the outage and standby five seconds after the server restarted. Home Assistant subsequently reported synchronized rules. The execution counter stayed at zero during this test.

On another restart, the first peer subscription completed 47 seconds after the Reflex runtime started. All eight peers had established a subscription by 100 seconds. The board reported 275,688 bytes of free internal heap when the eighth peer subscribed. Allow for this reconnection interval when testing immediately after boot.

In a Bureau test the same day, the USB log showed the XIAO active from 20:31:03 to 20:31:33 local time. During the test the tester saw the ceiling light turn on after the upper-button double press and turned it back off. The board’s execution count rose from zero to two, and the corresponding Home Assistant double-press automation did not run. The tester also observed the Bureau light and Zemismart blind respond to long presses. Home Assistant recorded an upper-button long press at 20:31:36, after the board had returned to standby, so the outage test established direct control of the ceiling light only.
