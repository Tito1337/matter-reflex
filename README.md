# Matter Reflex

Keep a Matter button working when Home Assistant or its Matter Server goes offline. Matter Reflex pairs a Home Assistant integration with a **Seeed Studio XIAO ESP32-C6** on your Thread network. Add the `reflex` label to a supported automation and the board stores its rules. If Home Assistant stops renewing a ten-second lease, the board can send commands directly to your lights, plugs and shades.

[Quick start](docs/quickstart.en.md) · [Firmware setup](firmware/README.md) · [Troubleshooting](docs/troubleshooting.md) · [Contributing](CONTRIBUTING.md)

## Your first automation

This example turns on a reading lamp at 70% brightness when you double-press a button. Open the YAML editor for a Home Assistant automation and replace the two entity IDs with your own. The button must advertise `multi_press_2` among its event types.

```yaml
alias: Double-press for reading light
triggers:
  - trigger: event.received
    target:
      entity_id: event.reading_button
    options:
      event_type: [multi_press_2]
actions:
  - action: light.turn_on
    target:
      entity_id: light.reading_lamp
    data:
      brightness_pct: 70
mode: single
```

After installing Reflex, enable the automation and give it the **reflex** label. Check **Synchronized** to confirm that the board received the accepted rules. Then check **Settings → System → Repairs** to see whether Reflex excluded any automations.

## Supported actions

Use a Matter button event (`event.received`) as the trigger. Specify each target by entity ID. The button, board and targets must belong to the same Matter fabric, the trust domain where Home Assistant commissioned them.

| Target | Supported actions |
| --- | --- |
| Light | `light.turn_on`, `light.turn_off`, `light.toggle`. When turning on a dimmable light, set the brightness or adjust it by a step, using Home Assistant brightness units or a whole-number percentage. |
| Smart plug | `switch.turn_on`, `switch.turn_off`, `switch.toggle` on the plug's power outlet. |
| Shade | `cover.open_cover`, `cover.close_cover`, `cover.stop_cover`, `cover.set_cover_position`, depending on the commands the device advertises. Home Assistant measures position from 0 (closed) to 100 (open). |

The board stores up to **32 gesture–target pairs**. A gesture that controls three devices uses three entries. A toggle requires the board to have received the target's on/off state. Conditions, delays, area targets, `choose` branches, `parallel` blocks, colors and transitions require Home Assistant. To run two independent actions on a long press, you can give two automations the same trigger.

## Installation

1. Prepare Home Assistant with Matter, a working Thread network and border router, a XIAO ESP32-C6 and a USB data cable. The build instructions use Linux with rootless Podman.
2. Build and provision the firmware, then commission the XIAO onto Home Assistant's Matter fabric. The supplied profile uses the SDK's development attestation credentials. [Firmware guide](firmware/README.md)
3. Copy the [matter_reflex integration directory](custom_components/matter_reflex) into `config/custom_components/`. Check your Home Assistant configuration and restart Core. Under **Settings → Devices & services**, add **Matter Reflex** and select your board. If prompted for the transfer key, enter the one you provisioned on the board.
4. Try the double press above. During normal operation, **Synchronized** is on, **Active** is off, and Home Assistant controls the lamp.

## How failover works

- **While Home Assistant is available**, it schedules a lease renewal every four seconds on a monotonic clock by invoking **Renew Reflex lease**, a named action on the XIAO (firmware 1.3.0, integration 0.5.1 or newer). The standard Matter `InstantAction` command renews a ten-second deadline held on the board. The XIAO stays subscribed to button events and ignores presses while the lease is valid.
- **When the lease expires**, the XIAO uses its Matter sessions to send commands directly to the targets. The next lease renewal returns it to standby. The **Executions** counter includes failed commands. Each `RuleExecuted` event carries the individual result.
- **When rules change**, Home Assistant compiles them, grants access to the required endpoints and verifies the fingerprint of the rule table saved in the board's nonvolatile memory. Home Assistant transfers the rules through development cluster `0xFFF1FC00` on endpoint 1. If Matter Server does not support that cluster, the integration uses HTTP over Thread IPv6 on port 5581, authenticated with HMAC-SHA256 and a single-use challenge. The board sends commands to the devices over Matter in either case. Avoid editing an endpoint's access control list during synchronization.

Home Assistant listens for status changes through Matter Server’s existing subscription. It reads the board on connection and during rule synchronization. If the server exposes only some or none of the status changes, a five-minute check bounds how stale the display can become while the board is reachable. [Firmware and traffic details](firmware/README.md#lease-and-rule-execution)

The lease measures whether Home Assistant can reach the XIAO. If an individual automation fails while Home Assistant continues renewing the lease, Home Assistant still handles that automation. A press during the interval between an outage and lease expiry can be lost. During recovery, both controllers may see the same press, so a command can execute twice.

## Hardware tests

With integration 0.5.1 and firmware 1.3.0, the fixed four-second schedule averaged **48.6 packets per minute** between Home Assistant and the XIAO. The board stayed in standby through one missed renewal and took over after ten seconds of silence. [Traffic measurements and failover tests](docs/lease-measurements.md)

The following observations come from a XIAO ESP32-C6 running firmware 1.1.0, integration 0.3.0, Home Assistant Core 2026.11.1 and Matter Server 9.3.0.

| Test | Observed result |
| --- | --- |
| BILRESA button and KAJPLATS light | A double press turned the light on at 70% with Home Assistant available and again with Matter Server stopped. |
| Six gestures on one BILRESA | With Matter Server stopped and the Matter integration suspended, the six gestures controlled a GRILLPLATS plug, two lights and a Zemismart shade. The shade opened to 75% and closed. The counter recorded nine results during the sequence. A tenth appeared during recovery and could not be attributed to a particular gesture. |
| XIAO restart and Home Assistant recovery | The board rejoined Thread, restored its rules and returned to standby when Home Assistant renewed the lease. |

To repeat an outage test, read the counter over USB and confirm `active=1` before pressing the button. Home Assistant can restart Matter Server automatically. Watch the physical target, then verify that the board reports `active=0` after recovery. Precise latency and behavior after simultaneous loss of the border router and Matter sessions still need measurement.

## Development

An experimental [ESPHome build for the XIAO ESP32-C6](examples/README.md) uses the same rule engine and Home Assistant integration.

The [contribution guide](CONTRIBUTING.md) covers the Python tests, the binary contract test with the C++ engine, and the target firmware build from a fresh clone. The [Home Assistant compiler](custom_components/matter_reflex/compiler.py) defines which automations Reflex accepts. The [firmware engine](firmware/main/reflex_engine.cpp) decodes those rules. The [tests](tests/) cover the shared format, Matter permissions and engine behavior.

## License and commercial contact

Matter Reflex is available under the [Mozilla Public License 2.0](LICENSE), which permits commercial use.

If you're building a commercial product with Reflex, I'd love to hear what you're making before you launch. You're welcome to [open an issue](https://github.com/Tito1337/matter-reflex/issues) to get in touch. Contact is optional.
