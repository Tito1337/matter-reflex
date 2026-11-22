# Matter Reflex for the ESPHome XIAO ESP32-C6

The [board configuration](xiao-esp32c6.yaml) uses ESPHome 2026.11.0 and pins the community Matter component to a Git revision. ESPHome owns Thread and commissioning. The local `matter_reflex` component registers endpoints 1 and 2, runs the shared firmware engine, and receives the four-second lease renewals through Matter Server. Home Assistant still compiles the labeled automations and grants the board access to the peers.

For a board that already runs ESPHome, [add Reflex to your existing node](../docs/esphome-component.md). The board-independent package lets you choose the sensors, GPIOs and endpoints.

For **direct reflexes**, build the local OTA profile from the same checkout as the integration. Choose the fresh-board or commissioned-board profile in [Thread OTA](#thread-ota). The Device Builder revision below supports fallback reflexes only.

## Install as an ESPHome package

In your Device Builder configuration, import the board package at the revision below and set your own device name and secrets locally. This example is for a fresh board. For a commissioned standalone board follow the migration instructions below.

```yaml
packages:
  reflex: github://Tito1337/matter-reflex/examples/xiao-base.yaml@14d9e91fae9826566f1faa1e6f9701b5b6b17f2d
  management: github://Tito1337/matter-reflex/examples/encrypted-api.yaml@14d9e91fae9826566f1faa1e6f9701b5b6b17f2d
  updates: github://Tito1337/matter-reflex/examples/ota.yaml@14d9e91fae9826566f1faa1e6f9701b5b6b17f2d

substitutions:
  reflex_api_key: !secret reflex_api_key

esphome:
  name: bureau-reflex

openthread:
  tlv: !secret thread_tlv
```

The pinned revision selects the Reflex code and its tested Matter dependency. The checkout examples override the component source to build local changes.

The package defines an internal binary sensor named `reflex_active`. Its `on_press` and `on_release` automations light the XIAO user LED at takeover and turn it off at handback. The packaged `on_rule_result` automation logs each rule ID, source event number and command status over USB. Status zero means success. [Command result codes](../docs/troubleshooting.md)

The subscribed and required peer sensors sample the local Matter state every five seconds and publish changes. The required count includes each distinct button or target node once. Reset reason and minimum free heap use ESPHome’s debug component.

## Encrypted ESPHome management

Include `examples/encrypted-api.yaml` as a second package and set the local substitution `reflex_api_key: !secret reflex_api_key` to a unique ESPHome encryption key. Add the device to the ESPHome integration in Home Assistant using its Thread IPv6 address and that key. The native API exposes peer and heap diagnostics and the `reflex_exchange` action. An empty image reads status. A complete hexadecimal rules image is validated and committed only for the specified 16-digit compressed fabric and node IDs. The response contains the installed hash. The lease continues to travel through Matter Server, and API client loss does not reboot the device.

In Home Assistant, select the encrypted board in the Reflex transfer setup. For an existing Reflex entry, use **Reconfigure**. The integration verifies the selected board’s Matter fabric and node before accepting it and checks the installed hash after each write.

## Build the HTTP-compatible profile

1. Clone the repository. The external component includes the shared C++ sources.
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

## Thread OTA

For a **new board**, use `xiao-esp32c6-ota.yaml` for its first USB installation. Set a unique `reflex_api_key` in your local secrets along with the Thread dataset. OTA requires encryption and shares the board’s native API key. Pair it on-network and add the encrypted ESPHome integration before setting up Matter Reflex. The package equivalents for a Device Builder installation are `xiao-base.yaml`, `encrypted-api.yaml` and `ota.yaml` at the same release tag. Supply the substitutions from local secrets.

For a **commissioned board using the standalone partition table**, build `xiao-esp32c6-existing-ota.yaml`. It preserves NVS, PHY and factory data and splits the former application space into two slots. Make a current private full-flash backup first. Verify the current table matches the standalone table as described above and that both new slots fit the built image. Install the bootloader, partition table, first application and initial OTA selection data at the individual offsets from the generated `flash_args`. Use individual segments so the gaps containing commissioner and fabric data are preserved.

```sh
./dev esphome compile examples/xiao-esp32c6-existing-ota.yaml
./dev usb bash -ec 'cd /work/examples/.esphome/build/matter-reflex/build && python -m esptool --chip esp32c6 --port "$ESPPORT" write_flash @flash_args'
```

Use this USB command only for the initial layout conversion. Subsequent updates use the same profile over routable Thread IPv6 from the Device Builder host. The ESPHome OTA uploader writes the inactive slot. Keep the private USB backup for rollback.

```sh
esphome upload examples/xiao-esp32c6-existing-ota.yaml --device YOUR_THREAD_IPV6_ADDRESS
```

After the USB installation and again after an OTA update, verify the same commissioned node, saved rules hash, peer readiness and standby on lease renewal.

## Hardware observations

On November 19, 2026, the application-only profile booted on an already commissioned XIAO ESP32-C6. It restored the same Matter node ID and rule-table SHA-256. The table contained 17 rules across eight peers.

With Home Assistant running, Matter Server was stopped for 32 seconds. The board’s USB log recorded takeover during the outage and standby five seconds after the server restarted. Home Assistant subsequently reported synchronized rules. The execution counter stayed at zero during this test.

On another restart, the first peer subscription completed 47 seconds after the Reflex runtime started. All eight peers had established a subscription by 100 seconds. The board reported 275,688 bytes of free internal heap when the eighth peer subscribed. Allow for this reconnection interval when testing immediately after boot.

In a Bureau test the same day, the USB log showed the XIAO active from 20:31:03 to 20:31:33 local time. During the test the tester saw the ceiling light turn on after the upper-button double press and turned it back off. The board’s execution count rose from zero to two, and the corresponding Home Assistant double-press automation did not run. The tester also observed the Bureau light and Zemismart blind respond to long presses. Home Assistant recorded an upper-button long press at 20:31:36, after the board had returned to standby, so the outage test established direct control of the ceiling light only.
