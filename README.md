# Matter Reflex

Choose who handles a Matter sensor or button trigger, even when Home Assistant is online. Matter Reflex pairs a Home Assistant integration with a **Seeed Studio XIAO ESP32-C6** on your Thread network. With a **direct reflex**, the ESP handles the automation locally. With a **fallback reflex**, Home Assistant normally handles it and the ESP takes over during an outage.

[Install on a XIAO](examples/README.md) · [Extend your ESPHome node](docs/esphome-component.md) · [Home Assistant quick start](docs/quickstart.en.md) · [Troubleshooting](docs/troubleshooting.md) · [Contributing](CONTRIBUTING.md)

## Your first automation

### IKEA MYGGBETT door opens → hall light on

Replace the entity IDs with your own, enable the automation, and apply the **reflex** label. An equivalent Matter contact sensor from another brand uses the same rule.

```yaml
alias: Hall light when the door opens
triggers:
  - trigger: state
    entity_id: binary_sensor.front_door
    from: "off"
    to: "on"
actions:
  - action: light.turn_on
    target:
      entity_id: light.hall
mode: single
```

### IKEA MYGGSPRAY detects motion → hall light brightens

Use the motion or occupancy binary sensor. Home Assistant exposes detected motion as `on`. This example sets a dimmable hall light to brightness 200 with a short fade.

```yaml
alias: Hall light on motion
triggers:
  - trigger: state
    entity_id: binary_sensor.hall_motion
    to: "on"
actions:
  - action: light.turn_on
    target:
      entity_id: light.hall
    data:
      brightness: 200
      transition: 0.2
mode: single
```

### Button example

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

Give every automation managed by Matter Reflex the **reflex** label. With the label alone, a supported automation runs in Home Assistant and the ESP takes over during an outage (**Fallback reflex**).

For **Direct reflex**, keep the label and add an enabled, top-level **Reflex mode** condition under **And if**. Select the ESP and choose **Direct reflex**. You can also select **Fallback reflex** explicitly to choose a particular ESP. On an unlabeled automation the Reflex condition lets HA proceed, and the compiler skips the automation. Other HA conditions still apply.

Save and check **Synchronized** and **Repairs** after changing a label or mode. Removing the label returns automatic handling to HA and removes the ESP rules on the next successful sync. Test with the physical trigger. HA’s **Run actions** executes the actions on HA and bypasses conditions.

For independent assignments, keep the **reflex** label and choose **Reflex handlers (advanced)** in place of **Reflex mode**. Both **Normal handler** and **Degraded handler** offer **Home Assistant**, **ESP**, and **None**. A normal handler continues to run even if its backup disappears. A degraded-only handler runs on the next matching trigger after it loses contact with the other controller. For a complex HA routine with a simpler outage version, keep the HA automation as usual and give a second automation the same trigger, **None** as normal handler, and **ESP** as degraded handler.

Upgrade the integration and ESP firmware together before enabling direct mode. Old firmware rejects an image containing a direct rule and retains its last accepted image. For an unreleased checkout, the local XIAO OTA profiles in `examples/` build the matching firmware from `../components`. Edits and disabling take effect on the ESP after its next successful synchronization. If an edit fails, the ESP keeps handling its installed gestures and targets. HA can handle newly added gestures while the edit is pending. Check **Repairs** before relying on an edited automation.

**Activity.** The ESP records each target command it handles for a direct reflex or during fallback. HA combines the results for each run into one Activity entry on the automation, including command rejections and transport errors. Activity records when HA received the results. The ESP keeps the most recent 32 target results in RAM for catch-up, scoped to the current rule image and Matter fabric. A restart, replacement of the image, or a full journal can leave gaps. HA's native traces and Last triggered describe HA executions.

## Supported triggers

Reflex resolves each HA entity to its commissioned node, endpoint, cluster, and attribute. The board and all peers must belong to the same Matter fabric. Sensor subscriptions stay live while HA controls the outputs.

| Sensor | Matter capability | HA trigger |
| --- | --- | --- |
| Door or window contact | Contact Sensor device type, Boolean State / StateValue | `state` to `"on"` for open or `"off"` for closed. Matter reports true for closed. |
| Motion or occupancy | Occupancy Sensing / Occupancy, bit 0 | `state` to `"on"` for occupied or `"off"` for clear. |
| Water leak | Water Leak Detector device type, Boolean State / StateValue | `state` to `"on"` for wet or `"off"` for dry. Matter reports true for wet. |
| Temperature | Temperature Measurement / MeasuredValue | `numeric_state` with one fixed `above` or `below` in the HA sensor’s °C, °F or K unit. |
| Relative humidity | Relative Humidity Measurement / MeasuredValue | `numeric_state` with one fixed `above` or `below` in percent. |
| Button | Switch events | `event.received` with an advertised gesture or multi-press count. |

For example, to act when humidity rises above 60%, use this trigger with any supported action.

```yaml
triggers:
  - trigger: numeric_state
    entity_id: sensor.bathroom_humidity
    above: 60
```

The first valid report after startup, rule replacement or sensor reconnection establishes a baseline. A null or invalid reading clears it. A rule fires once when a subsequent report enters the selected state while the ESP handles that rule. A numeric reading equal to the threshold is outside the selected range and rearms the rule. Reflex compares the reported measurement at Matter’s native hundredth-unit resolution. Fallback rules track sensor changes during standby and wait for a fresh crossing after takeover.

## Supported actions

Specify each target by HA entity ID.

| Target | Supported actions |
| --- | --- |
| Light | `light.turn_on`, `light.turn_off`, `light.toggle`. When turning on a dimmable light, set the brightness or adjust it by a step, using Home Assistant brightness units or a whole-number percentage. A transition-capable light accepts a brightness setting or step with a fade in seconds (tenths on the wire). A level of zero turns the light off. |
| Smart plug | `switch.turn_on`, `switch.turn_off`, `switch.toggle` on the plug’s primary On/Off outlet, including generic Matter outlets exposed by HA. |
| Shade | `cover.open_cover`, `cover.close_cover`, `cover.stop_cover`, `cover.set_cover_position`, depending on the commands the device advertises. For tilt, use `cover.set_cover_tilt_position` with `tilt_position`, or `cover.open_cover_tilt`, `cover.close_cover_tilt` and `cover.stop_cover_tilt` where HA exposes them. Home Assistant measures position from 0 (closed) to 100 (open). |

The board stores up to **32 trigger–target pairs**. A trigger that controls three devices uses three entries. On supported lights and plugs, Reflex sends a native Matter Toggle for each matching gesture. A direct button can toggle as soon as its fresh event reaches the board and the target has an active secure session. For targets that advertise only On and Off, Reflex chooses the command from the last reported power state. Conditions other than the Reflex handler selection, delays, area targets, `choose` branches, `parallel` blocks, colors and delayed triggers (`for`) require Home Assistant. To run two independent actions on a long press, you can give two automations the same trigger.

## Installation

1. Prepare Home Assistant with Matter, a working Thread network and border router, a XIAO ESP32-C6 and a USB data cable. Use ESPHome Device Builder or the pinned Linux build container.
2. Install the [ESPHome XIAO package](examples/README.md) and commission the board on-network. The package supports local takeover indication, per-rule logging, peer diagnostics and encrypted management and updates over Thread. For an existing standalone board, follow the commissioning-preserving USB migration in the same guide. The [standalone firmware guide](firmware/README.md) covers the original build and recovery.
3. Add the board to Home Assistant’s ESPHome integration using its API encryption key and current Thread IPv6 address. Copy the [matter_reflex integration directory](custom_components/matter_reflex) into `config/custom_components/`. Check your Home Assistant configuration and restart Core. Under **Settings → Devices & services**, add **Matter Reflex** and select your board. If prompted for a rules transport, select the board already connected through the encrypted ESPHome integration.
4. Try the door automation above. With the label alone, Home Assistant controls the lamp while **Synchronized** is on and **Active** is off. For direct operation, add the **Reflex mode** condition described above.

HTTP management has been removed. For an existing HTTP installation, add the board’s encrypted ESPHome connection, update the integration and reconfigure its entry. Follow the [migration steps](examples/README.md#upgrade-an-http-installation); commissioning and the saved rules can be retained.

## How failover works

- **While Home Assistant is available**, it schedules a lease renewal every four seconds on a monotonic clock by invoking **Renew Reflex lease**, a named action on the XIAO (firmware 1.3.0, integration 0.5.1 or newer). The standard Matter `InstantAction` command renews a ten-second deadline held on the board. The ESP stays subscribed to sensor reports and button events. It handles direct rules on matching triggers and fallback rules after the lease expires. Each successful renewal response also tells HA the ESP is reachable. For a direct reflex, HA checks the installed image and the readiness of the specific source endpoint. After restarting, HA restores its saved rule map and checks it against the ESP even if another automation cannot compile. HA allows up to ten seconds at startup to establish ownership. Once delegated, HA takes over after ten seconds without a renewal response. A ten-second startup grace period also applies when the selected integration entry cannot load.
- **When the lease expires**, the XIAO uses its Matter sessions to send commands directly to the targets. The next lease renewal returns fallback rules to standby. The Active indicator shows fallback takeover. The **Executions** counter includes failed commands. Each `RuleExecuted` event carries the individual result.
- **When rules change**, Home Assistant compiles them, grants access to the required endpoints and verifies the fingerprint of the rule table saved in the board's nonvolatile memory. Home Assistant transfers the rules through development cluster `0xFFF1FC00` on endpoint 1. When Matter Server lacks that cluster, the integration uses the encrypted ESPHome API over Thread IPv6. The board sends commands to the devices over Matter in either case. Avoid editing an endpoint's access control list during synchronization.

Home Assistant listens for status changes through Matter Server’s existing subscription. It reads the board on connection and during rule synchronization. If the server exposes only some or none of the status changes, a five-minute check bounds how stale the display can become while the board is reachable. [Firmware and traffic details](firmware/README.md#lease-and-rule-execution)

The lease measures whether Home Assistant can reach the XIAO. Reachability selects the backup. An individual action failure leaves the normal handler in charge. A press during the interval between an outage and lease expiry can be lost. During a handoff, recovery or asymmetric network failure, both controllers may see the same press and reach its target. A command can execute twice in either mode. A missing execution report never triggers a retry.

## Simulator-tested support

The offline Matter.js test commissions a multi-endpoint sensor node and a separate actuator node. It passes real subscription reports to the host build of the firmware rule engine and lease clock, then sends the resolved commands over Matter. The test covers door opening → light on, occupancy → brightness fade, leak → plug on, temperature falling below freezing → shade tilt to 25%, and humidity above 55% → light off. It also checks an ESP-first leak rule with a healthy HA lease, duplicate reports, reconnection with an already-open door, and fallback rules returning to standby. Run `./dev test-sensors` to reproduce it.

The on-demand firmware regression tests direct, fallback and advanced modes on disposable Home Assistant fabrics. Two emulated ESP32-C6s run Reflex and its Thread border router. Two button gestures and five sensor changes drive a lamp, dimmer, plug and two shades. Each actuator records the authenticated sender, command arguments and resulting state. In direct mode the suite checks that the ESP handles the rules while HA is online, that HA takes over when the ESP stops, and that the same ESP resumes after a cold boot. [Run the firmware regression locally](tests/lab/README.md#firmware-takeover-regression).

MYGGBETT and MYGGSPRAY are configuration examples. Their standard capabilities were exercised with simulated devices. Physical confirmation of those models, leak and environmental triggers, brightness fades, and shade tilt is still pending.

Update the board firmware and HA integration from this checkout before using the new capabilities. Firmware still loads stored version 1 rule images. The integration emits version 2 when a rule uses a sensor, tilt, or fade. Existing button-only configurations using the original actions keep their previous image format.

## Hardware tests

On November 24, 2026, a tester confirmed direct operation of all six BILRESA gestures controlling a desk lamp, ceiling light and blind, plus a wall-button desk-lamp toggle, while Home Assistant was online. The seven gestures use ten independent automations. The subsequent HTTP-removal deployment preserved the commissioned Matter identity and rule hash; the encrypted ESPHome API reported all ten direct rules ready, all seven peers subscribed, and successful synchronization. Port 5581 refused connections. This check does not add physical confirmation for the sensor examples above.

With integration 0.5.1 and firmware 1.3.0, the fixed four-second schedule averaged **48.6 packets per minute** between Home Assistant and the XIAO. The board stayed in standby through one missed renewal and took over after ten seconds of silence. [Traffic measurements and failover tests](docs/lease-measurements.md)

The following observations come from a XIAO ESP32-C6 running firmware 1.1.0, integration 0.3.0, Home Assistant Core 2026.11.1 and Matter Server 9.3.0.

| Test | Observed result |
| --- | --- |
| BILRESA button and KAJPLATS light | A double press turned the light on at 70% with Home Assistant available and again with Matter Server stopped. |
| Six gestures on one BILRESA | With Matter Server stopped and the Matter integration suspended, the six gestures controlled a GRILLPLATS plug, two lights and a Zemismart shade. The shade opened to 75% and closed. The counter recorded nine results during the sequence. A tenth appeared during recovery and could not be attributed to a particular gesture. |
| XIAO restart and Home Assistant recovery | The board rejoined Thread, restored its rules and returned to standby when Home Assistant renewed the lease. |

To repeat an outage test, record the execution counter and wait for the XIAO takeover LED or the ESPHome USB log message `Lease state: active` before opening the door or pressing the button. Home Assistant can restart Matter Server automatically. Watch the physical target, then verify that the board reports standby after recovery. Precise latency and behavior after simultaneous loss of the border router and Matter sessions still need measurement.

## Development

Develop firmware on `main` as an ESPHome external component. The [extension example](examples/extend-node.yaml) shows how to keep your own sensors and Matter endpoints alongside Reflex. The last standalone baseline is tagged `standalone-v1.3.0`.

The [contribution guide](CONTRIBUTING.md) covers the Python tests, the binary contract test with the C++ engine, and the target firmware build from a fresh clone. The [Home Assistant compiler](custom_components/matter_reflex/compiler.py) defines which automations Reflex accepts. The [firmware engine](components/matter_reflex/reflex_engine.cpp) decodes those rules. The [tests](tests/) cover the shared format, Matter permissions and engine behavior.

## License and commercial contact

Matter Reflex is available under the [Mozilla Public License 2.0](LICENSE), which permits commercial use.

If you're building a commercial product with Reflex, I'd love to hear what you're making before you launch. You're welcome to [open an issue](https://github.com/Tito1337/matter-reflex/issues) to get in touch. Contact is optional.
