# Matter Reflex

Choose who handles a Matter button press, even when Home Assistant is online. Matter Reflex pairs a Home Assistant integration with a **Seeed Studio XIAO ESP32-C6** on your Thread network. With a **direct reflex**, the ESP handles the automation locally. With a **fallback reflex**, Home Assistant normally handles it and the ESP takes over during an outage.

[Install on a XIAO](examples/README.md) · [Extend your ESPHome node](docs/esphome-component.md) · [Home Assistant quick start](docs/quickstart.en.md) · [Troubleshooting](docs/troubleshooting.md) · [Contributing](CONTRIBUTING.md)

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

## Direct and fallback reflexes

| Reflex mode | Normal handler | Degraded handler |
| --- | --- | --- |
| **Direct reflex** | ESP | Home Assistant |
| **Fallback reflex** | Home Assistant | ESP |

Think of Home Assistant as the brain. A direct reflex follows a *spinal* pathway: the ESP responds to the button without consulting HA. A fallback reflex normally follows a *cortical* pathway through HA, with the ESP ready to take over.

In the HA automation editor, open **And if → Add condition**, search for **Reflex mode**, select the configured ESP and choose the mode. Keep this condition enabled at the top level. Save the automation and check **Synchronized** and **Repairs** before using the new policy. To test routing, use the physical trigger. HA’s **Run actions** deliberately executes the actions on HA and bypasses conditions. Existing automations labeled **reflex** continue to use fallback mode. An explicit Reflex condition selects its own ESP and takes precedence over that label. Ordinary HA automations need no setup.

For independent assignments, choose **Reflex handlers (advanced)** in place of **Reflex mode**. Both **Normal handler** and **Degraded handler** offer **Home Assistant**, **ESP**, and **None**. A normal handler continues to run even if its backup disappears. A degraded-only handler runs on the next matching trigger after it loses contact with the other controller. For a complex HA routine with a simpler outage version, keep the HA automation as usual and give a second automation the same trigger, **None** as normal handler, and **ESP** as degraded handler.

Upgrade the integration and ESP firmware together before enabling direct mode. Old firmware rejects an image containing a direct rule and retains its last accepted image. When using the remote ESPHome package before a release, set its external component source to the revision containing these changes. Edits and disabling take effect on the ESP after its next successful synchronization.

**Activity.** For a direct reflex, the ESP acts immediately and records the outcome of each target command. HA groups these results into one Activity entry on the automation, including command rejections and transport errors. Activity records when HA received the results. The ESP keeps the most recent 32 direct target results in RAM for catch-up, scoped to the current rule image and Matter fabric. A restart, replacement of the image, or a full journal can leave gaps. HA's native traces and Last triggered describe HA executions.

## Supported actions

Use a Matter button event (`event.received`) as the trigger. Specify each target by entity ID. The button, board and targets must belong to the same Matter fabric, the trust domain where Home Assistant commissioned them.

| Target | Supported actions |
| --- | --- |
| Light | `light.turn_on`, `light.turn_off`, `light.toggle`. When turning on a dimmable light, set the brightness or adjust it by a step, using Home Assistant brightness units or a whole-number percentage. |
| Smart plug | `switch.turn_on`, `switch.turn_off`, `switch.toggle` on the plug's power outlet. |
| Shade | `cover.open_cover`, `cover.close_cover`, `cover.stop_cover`, `cover.set_cover_position`, depending on the commands the device advertises. Home Assistant measures position from 0 (closed) to 100 (open). |

The board stores up to **32 gesture–target pairs**. A gesture that controls three devices uses three entries. A toggle requires the board to have received the target's on/off state. Conditions other than the Reflex handler selection, delays, area targets, `choose` branches, `parallel` blocks, colors and transitions require Home Assistant. To run two independent actions on a long press, you can give two automations the same trigger.

## Installation

1. Prepare Home Assistant with Matter, a working Thread network and border router, a XIAO ESP32-C6 and a USB data cable. Use ESPHome Device Builder or the pinned Linux build container.
2. Install the [ESPHome XIAO package](examples/README.md) and commission the board on-network. The package supports local takeover indication, per-rule logging, peer diagnostics and encrypted management and updates over Thread. For an existing standalone board, follow the commissioning-preserving USB migration in the same guide. The [standalone firmware guide](firmware/README.md) covers the original build and recovery.
3. Copy the [matter_reflex integration directory](custom_components/matter_reflex) into `config/custom_components/`. Check your Home Assistant configuration and restart Core. Under **Settings → Devices & services**, add **Matter Reflex** and select your board. If prompted for a rules transport, select the encrypted ESPHome board or enter its provisioned HTTP transfer key.
4. Try the double press above. During normal operation, **Synchronized** is on, **Active** is off, and Home Assistant controls the lamp.

## How failover works

- **While Home Assistant is available**, it schedules a lease renewal every four seconds on a monotonic clock by invoking **Renew Reflex lease**, a named action on the XIAO (firmware 1.3.0, integration 0.5.1 or newer). The standard Matter `InstantAction` command renews a ten-second deadline held on the board. The ESP stays subscribed to button events. It handles direct rules on every matching press and fallback rules after the lease expires. Each successful renewal response also tells HA the ESP is reachable. HA takes over direct rules after ten seconds without confirmation, with the same initial quiet period on startup.
- **When the lease expires**, the XIAO uses its Matter sessions to send commands directly to the targets. The next lease renewal returns fallback rules to standby. The Active indicator shows fallback takeover. The **Executions** counter includes failed commands. Each `RuleExecuted` event carries the individual result.
- **When rules change**, Home Assistant compiles them, grants access to the required endpoints and verifies the fingerprint of the rule table saved in the board's nonvolatile memory. Home Assistant transfers the rules through development cluster `0xFFF1FC00` on endpoint 1. When Matter Server lacks that cluster, the integration can use the encrypted ESPHome API or authenticated HTTP over Thread IPv6 on port 5581. The board sends commands to the devices over Matter in either case. Avoid editing an endpoint's access control list during synchronization.

Home Assistant listens for status changes through Matter Server’s existing subscription. It reads the board on connection and during rule synchronization. If the server exposes only some or none of the status changes, a five-minute check bounds how stale the display can become while the board is reachable. [Firmware and traffic details](firmware/README.md#lease-and-rule-execution)

The lease measures whether Home Assistant can reach the XIAO. Reachability selects the backup. An individual action failure leaves the normal handler in charge. A press during the interval between an outage and lease expiry can be lost. During an asymmetric network failure or recovery, both controllers may see the same press and reach its target. A command can execute twice in either mode. A missing execution report never triggers a retry.

## Hardware tests

With integration 0.5.1 and firmware 1.3.0, the fixed four-second schedule averaged **48.6 packets per minute** between Home Assistant and the XIAO. The board stayed in standby through one missed renewal and took over after ten seconds of silence. [Traffic measurements and failover tests](docs/lease-measurements.md)

The following observations come from a XIAO ESP32-C6 running firmware 1.1.0, integration 0.3.0, Home Assistant Core 2026.11.1 and Matter Server 9.3.0.

| Test | Observed result |
| --- | --- |
| BILRESA button and KAJPLATS light | A double press turned the light on at 70% with Home Assistant available and again with Matter Server stopped. |
| Six gestures on one BILRESA | With Matter Server stopped and the Matter integration suspended, the six gestures controlled a GRILLPLATS plug, two lights and a Zemismart shade. The shade opened to 75% and closed. The counter recorded nine results during the sequence. A tenth appeared during recovery and could not be attributed to a particular gesture. |
| XIAO restart and Home Assistant recovery | The board rejoined Thread, restored its rules and returned to standby when Home Assistant renewed the lease. |

To repeat an outage test, record the execution counter and wait for the XIAO takeover LED or the ESPHome USB log message `Lease state: active` before pressing the button. Home Assistant can restart Matter Server automatically. Watch the physical target, then verify that the board reports standby after recovery. Precise latency and behavior after simultaneous loss of the border router and Matter sessions still need measurement.

## Development

Develop firmware on `main` as an ESPHome external component. The [extension example](examples/extend-node.yaml) shows how to keep your own sensors and Matter endpoints alongside Reflex. The last standalone baseline is tagged `standalone-v1.3.0`.

The [contribution guide](CONTRIBUTING.md) covers the Python tests, the binary contract test with the C++ engine, and the target firmware build from a fresh clone. The [Home Assistant compiler](custom_components/matter_reflex/compiler.py) defines which automations Reflex accepts. The [firmware engine](components/matter_reflex/reflex_engine.cpp) decodes those rules. The [tests](tests/) cover the shared format, Matter permissions and engine behavior.

## License and commercial contact

Matter Reflex is available under the [Mozilla Public License 2.0](LICENSE), which permits commercial use.

If you're building a commercial product with Reflex, I'd love to hear what you're making before you launch. You're welcome to [open an issue](https://github.com/Tito1337/matter-reflex/issues) to get in touch. Contact is optional.
