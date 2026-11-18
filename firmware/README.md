# XIAO ESP32-C6 firmware

The `dev` script pins Espressif's official `release-v1.6_idf_v5.5.4` container
image by digest. It includes ESP-IDF 5.5.4, ESP-Matter at
`33b042e85360dd7a352167a6cba204f7087eba67`, and connectedhomeip at
`efefc94fee39d8d1fbbc3c27b9d7fc9025095887`.
[Espressif's light switch example](https://github.com/espressif/esp-matter/tree/33b042e85360dd7a352167a6cba204f7087eba67/examples/light_switch)

## Build the firmware

Run these commands from the repository root. You can build without connecting the board.

```sh
./dev matter idf.py -C firmware build
./dev matter idf.py -C firmware size
```

The build produces `firmware/build/matter_reflex.bin`. The partition table
reserves 4,063,232 bytes for the application. The rest of the 4 MiB flash
holds the bootloader, partition table, radio data, nonvolatile storage (NVS),
and factory data partition (`fctry`). The build enables OpenThread in
router-capable Full Thread Device (FTD) mode, the SRP and DNS clients, IPv6,
and the USB Serial/JTAG console. Thread uses the integrated 802.15.4 radio.
Install application updates over USB. [Partition table](partitions.csv)

## Select the USB board

Connect the XIAO with a USB data cable and list the stable serial device IDs.

```sh
ls -l /dev/serial/by-id/
```

Find the symlink for your XIAO and replace `YOUR_XIAO_ID` below with its name.
If several boards are connected, unplug the XIAO and plug it back in to see
which symlink reappears. The script uses that name to identify the board's
pairing credentials.

```sh
git config --local matter-reflex.board '/dev/serial/by-id/YOUR_XIAO_ID'
```

To select a different board or use a copy of the project without Git, set
`MATTER_REFLEX_BOARD` to the full path of the USB symlink. This variable
takes precedence over the Git setting. The user running Podman needs read
and write access to the serial device.

## Generate pairing credentials

```sh
./dev factory
```

This command uses CHIP's factory data generator to create pairing credentials
for the selected USB ID. Running it again reuses the same codes. The private
file `onboarding_codes.csv` contains the board's Matter QR code payload and
manual pairing code. The partition image `factory_partition.bin` contains the
SPAKE2+ verifier. Both files are stored in
`${XDG_DATA_HOME:-$HOME/.local/share}/matter-reflex/<USB-ID>/factory/`.
The script replaces colons in the USB ID with hyphens in the directory name.

The board uses CHIP development attestation, test vendor ID `0xFFF1`, and
product ID `0x8000`. The Matter commissioner must accept this test attestation.
The board identifies itself as **Matter Reflex**.

## Flash the board

If the board already runs other firmware, **back up all 4 MiB of flash**
before the first installation. The new partition table assigns the flash to
Matter Reflex. Run the following command from the repository root to save a
copy in a private directory ignored by Git. It prints the backup path after
verifying that the file contains exactly 4,194,304 bytes.

```sh
./dev usb bash -ec 'umask 077; mkdir -p private; dir=$(mktemp -d private/xiao.XXXXXX); python -m esptool --chip esp32c6 -p "$ESPPORT" read_flash 0 0x400000 "$dir/flash.bin" && test "$(wc -c < "$dir/flash.bin")" -eq 4194304 && echo "$dir/flash.bin"'
```

The following commands write to flash. `./dev matter-usb` selects the port
by USB ID.

1. Flash the bootloader, partition table, and firmware.

   ```sh
   ./dev matter-usb idf.py -C firmware flash
   ```

2. Write this board's factory data. The command looks up the `fctry` partition
   by name in the partition table on the board.

   ```sh
   ./dev matter-usb bash -c 'python "$IDF_PATH/components/partition_table/parttool.py" --port "$ESPPORT" --partition-table-offset 0xc000 write_partition --partition-name fctry --input /factory/factory_partition.bin'
   ```

3. Provision the Thread dataset over USB as described in the next section.

For later updates that use the same partition layout, flash only the application
with `./dev matter-usb idf.py -C firmware app-flash`. If you lose USB read or
write access after reconnecting the board, restore the device permissions using
your Linux distribution's usual method.

If you have used shade rules and want to downgrade from firmware 1.1 to 1.0,
remove the `reflex` label from those automations and wait for Home Assistant to
confirm synchronization before flashing. Firmware 1.0 can load only a rule
image that contains light rules exclusively.

## Join the Thread network

Sign in to Home Assistant as an administrator and open **Settings → Connectivity
→ Thread**. Select the network your remotes use. If Home Assistant needs the
network credentials, import them from a phone that already has them.
[Managing Thread credentials](https://www.home-assistant.io/integrations/thread/)

If the network details show the active operational dataset as a hexadecimal
TLV string, copy it. For an external border router, you can also retrieve it
using the WebSocket connection in your Home Assistant browser session. Open
the browser's developer console on the Home Assistant page and list the networks.

```js
const ha = document.querySelector("home-assistant").hass
const { datasets } = await ha.callWS({ type: "thread/list_datasets" })
console.table(datasets.map(({ network_name, dataset_id, preferred }) => ({ network_name, dataset_id, preferred })))
```

In the same console, run the following snippet. Enter the `dataset_id` from
the row for your network, then copy the TLV string from the second prompt.
The dataset contains your Thread network key.

```js
const dataset_id = prompt("Enter the dataset_id for your network from the table")
if (dataset_id) {
  const { tlv } = await ha.callWS({ type: "thread/get_dataset_tlv", dataset_id })
  void prompt("Copy this dataset and paste it into the hidden terminal prompt.", tlv)
}
```

In a terminal, run the first command and paste the TLV string at the hidden
prompt. Then check that the board has joined the network.

```sh
./dev usb python /work/tools/board_console.py dataset
./dev usb python /work/tools/board_console.py status
```

The USB tool also accepts the full WebSocket JSON response on standard input
from a private file. The XIAO saves the dataset and opens a 15-minute
commissioning window. Wait until `status` shows `thread=attached`. If the window
expires before the first pairing, run `./dev usb python /work/tools/board_console.py pair`
to reopen it.

## Pair with Home Assistant

Run the following snippet in the browser's developer console on the Home
Assistant page. At the prompt, paste the `qrcode` value from your private
`onboarding_codes.csv` file. It starts with `MT:`.

```js
const pairingCode = prompt("Paste the XIAO's Matter QR code payload")
if (pairingCode) {
  await document.querySelector("home-assistant").hass.callWS({
    type: "matter/commission", code: pairingCode, network_only: true
  })
}
```

Check that **Matter Reflex** appears in the Matter integration and that the
USB `status` command shows `fabrics=1` and a node ID. Close the browser
console when you finish. This firmware commissions the board over Thread.
[Home Assistant WebSocket API](https://contributors.home-assistant.io/core/internals/websocket-api/)

If the Matter Reflex setup form asks for a transfer key, use your password
manager to generate **64 random hexadecimal digits** (32 bytes). Enter the
key at the hidden USB prompt, then enter the same key in the form. The first
authenticated status request binds the key to the node and its fabric.

```sh
./dev usb python /work/tools/board_console.py key
```

## Lease and rule execution

Home Assistant renews the lease on endpoint 2, a logical On/Off Plug-in Unit.
It sends `OnWithTimedOff` over a CASE session with administrator privileges
to pause local rule execution for the requested duration.

Endpoint 1 hosts the rules cluster and its vendor-specific `RuleExecuted`
event. Fields 0 through 3 contain the rule ID, local node ID, source event
number, and command status, respectively. Local status codes include `0x100`
for a missing session, `0x101` for an unknown device state, and `0x102` for
a transport error.

If the Thread border router goes offline, the XIAO can still use active CASE
sessions and each peer's last authenticated address. It keeps these addresses
in RAM. If both the cache and Thread discovery are unavailable, reconnection
depends on which services are still available on the mesh.
