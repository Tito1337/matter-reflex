# Matter Reflex

**Your light switches should keep working when Home Assistant goes down.**

A button and a light shouldn't need a running Home Assistant server just to talk to each other. Matter Reflex gives supported automations a home on a small **ESP32-C6 running ESPHome**, so the basics can keep working through a server restart or outage.

You still configure your automations in Home Assistant. Reflex copies the supported rules onto the ESP, which listens for Matter button presses and sensor reports and sends commands directly to the devices.

[Get started](#installation) · [Network modes](#thread-wi-fi-and-dual-mode) · [ESPHome installation guide](examples/README.md) · [Add Reflex to your ESPHome node](docs/esphome-component.md) · [Troubleshooting](docs/troubleshooting.md)

## Why "Reflex"?

Think of Home Assistant as the brain of your home. It handles the complex decisions, but switching a light shouldn't always need the brain's involvement.

A spinal reflex has its own path. Matter Reflex follows the same idea: the ESP knows what to do and can act without asking Home Assistant. Let it handle an automation all the time, or keep it ready as a backup.

## What you can do

- **Keep buttons and sensors useful during an outage.** The ESP stores its rules in flash and runs them independently of HA.
- **Send Matter commands directly from the ESP.** Button presses and sensor reports can trigger actions without going through HA or Matter Server. The network you choose determines which connections the automation still needs.
- **Choose who handles each automation.** Use the ESP for everyday operation or just for fallback.
- **Keep a simpler backup for a complex routine.** HA can run the full automation while the ESP handles a basic version during an outage.
- **Keep using Home Assistant's automation editor.** Apply a label, choose a mode and let Reflex synchronize the rules and Matter permissions.
- **See what the ESP did.** Command results appear in the automation's Activity when HA can receive them.

## Thread, Wi-Fi and dual mode

The published installation uses **Thread**. The [experimental Wi-Fi feature branch](https://github.com/Tito1337/matter-reflex/tree/feature/wifi-support) adds **Wi-Fi-only** and **Wi-Fi + Thread** configurations. See the [setup guide and test results](https://github.com/Tito1337/matter-reflex/blob/feature/wifi-support/docs/wifi-support.md) to try either mode.

| Configuration | How it connects |
| --- | --- |
| **Thread** | The ESP joins your Thread mesh. For sources and targets on that same mesh, the control path stays off your LAN. |
| **Wi-Fi only · Experimental** | The ESP joins your Wi-Fi network and controls Matter devices over the LAN. It can also reach Thread devices through an existing border router. |
| **Wi-Fi + Thread · Experimental** | The ESP joins both networks and runs ESPHome's built-in Thread border router, providing routing and discovery between the surviving mesh and LAN. |

Direct and fallback reflexes work the same way in all three configurations. Wi-Fi operation still needs a working access point and LAN; reaching Thread devices from a Wi-Fi-only ESP also needs a border router. In dual mode, the ESP can provide that border-router role itself, including when the HA host and its border router go offline.

Thread-to-Thread control is designed to keep working without the LAN or an external border router, provided the remaining mesh stays connected. In dual mode, battery-powered devices may need time to find a new parent after a router disappears.

Wi-Fi-only operation has been tested on a physical XIAO with HA Core stopped. Wi-Fi and dual-mode outage and recovery scenarios also have emulator coverage; dual-mode radio coexistence and sleepy-device handover still need hardware validation.

## Direct and fallback reflexes

| Mode | During normal operation | When the normal handler is unavailable |
| --- | --- | --- |
| **Direct reflex** | ESP handles the automation | Home Assistant takes over |
| **Fallback reflex** | Home Assistant handles the automation | ESP takes over |

Give a supported, enabled automation the **reflex** label to use **Fallback reflex**. To use **Direct reflex**, also add an enabled, top-level **Reflex mode** condition under **And if**, select your ESP and choose **Direct reflex**.

For more control, choose **Reflex handlers (advanced)** instead. Set the normal and degraded handlers independently to **Home Assistant**, **ESP** or **None**. For example, keep your full HA routine as usual and create a second, supported automation with the same trigger, **None** as its normal handler and **ESP** as its degraded handler. The simpler rule runs only when HA is unavailable.

## Your first automation

### Button example

Double-press an IKEA BILRESA to turn on a reading lamp at 70% brightness. Replace the entity IDs with your own. The button must expose the `multi_press_2` event in Home Assistant.

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

Enable the automation and apply the **reflex** label. Add the **Reflex mode** condition if you want the ESP to handle it directly.

Check **Synchronized** to confirm the board received the rules, and **Settings → System → Repairs** for any automations Reflex couldn't accept. Then test with the physical button. HA's **Run actions** bypasses conditions and executes the actions in HA.

## Supported triggers

Reflex supports standard Matter capabilities across brands.

| Trigger | What it can respond to |
| --- | --- |
| Buttons | Advertised gestures, including presses, long presses and multi-press events |
| Door and window contacts | Opening or closing |
| Motion and occupancy sensors | Occupied or clear |
| Water leak sensors | Wet or dry |
| Temperature sensors | Crossing a fixed threshold in °C, °F or K |
| Humidity sensors | Crossing a fixed percentage threshold |

Sensor rules react to a new state change or threshold crossing. The first valid report after startup or reconnection establishes the starting state; it doesn't trigger an action by itself. See the [contact and motion examples](examples/sensor-automations.yaml) for sensor configurations.

## Supported actions

| Target | Actions |
| --- | --- |
| Lights | On, off, toggle, brightness, relative brightness steps and supported brightness transitions |
| Smart plugs | On, off and toggle on the primary outlet |
| Blinds and shades | Open, close, stop, set position and supported tilt controls |

Sources, targets and the ESP must be commissioned on the **same Matter fabric**. Available actions depend on what each device advertises.

The ESP stores up to **32 trigger-to-target pairs**. One trigger controlling three devices uses three entries. Use explicit entity IDs for targets. Templates, other conditions, delays, area targets, colors, `choose` and `parallel` blocks stay in HA. Reflex reports unsupported automations in **Repairs**.

## Installation

The **Thread installation below** needs Home Assistant with Matter, a working Thread network and border router for setup, access to your Thread dataset, and a **Seeed Studio XIAO ESP32-C6** with a USB data cable. The firmware uses ESPHome's ESP-IDF framework and a development Matter attestation profile.

1. **Install the ESPHome firmware.** Follow the [XIAO installation guide](examples/README.md#thread-ota) using the fresh-board OTA profile. It includes encrypted management, updates over Thread and a takeover LED. You can also [add Reflex to an existing ESPHome node](docs/esphome-component.md).
2. **Connect the board.** Commission it through Home Assistant's Matter integration. Matter Server must [accept the development attestation](examples/README.md#commission-with-development-attestation). Then add it to the ESPHome integration using its encryption key and Thread IPv6 address.
3. **Install the HA integration.** Copy [the `matter_reflex` directory](custom_components/matter_reflex) into `config/custom_components/`, check your configuration and restart HA. Under **Settings → Devices & services**, add **Matter Reflex**, select the commissioned board and choose its encrypted ESPHome connection if prompted.
4. **Try the button example.** Apply the **reflex** label, choose your mode, and confirm **Synchronized** with no related **Repairs** issues.

For an outage test in fallback mode, take HA or Matter Server offline and wait for the takeover LED before pressing the button. [Home Assistant quick start](docs/quickstart.en.md)

## How failover works

Home Assistant renews a ten-second lease on the ESP every four seconds. The ESP stays subscribed to its buttons and sensors in both modes. Direct rules run on the ESP whether HA is online or offline; fallback rules begin running after the lease expires. When communication returns, fallback rules hand control back to HA. In direct mode, HA can take over if it loses contact with the ESP.

Rules are stored in the ESP's nonvolatile memory, and known peer addresses are cached to help it reconnect after a restart. Rule synchronization uses encrypted management, and device commands use authenticated Matter sessions.

Failover takes time: a trigger during the detection interval can be missed. During a handoff or an asymmetric network failure, both controllers may act on the same trigger, so duplicate commands are possible. Updates and disabling take effect on the ESP after a successful synchronization. **Synchronized** and **Repairs** show whether your latest changes have reached it.

## Hardware tests

**Experimental, and already running in a real home.** With the Thread configuration on November 24, 2026, all six BILRESA gestures controlled a desk lamp, ceiling light and blind with Home Assistant **offline**. A separate wall button also toggled the desk lamp. The seven gestures were configured through ten independent automations.

Earlier hardware checks covered BILRESA controlling a KAJPLATS light, a GRILLPLATS plug and a Zemismart shade with Matter Server stopped, plus rule restoration after an ESP restart and handback after HA recovered. [Failover timing and traffic measurements](docs/lease-measurements.md)

## Simulator-tested support

The test lab exercises button and sensor rules, light and plug commands, brightness fades, shade positioning and tilt. It checks direct, fallback and advanced handling, controller outages, duplicate reports and recovery after an ESP restart using Matter.js peers and emulated ESP32-C6 firmware.

Contact, motion, leak, temperature and humidity support, brightness fades and shade tilt have simulator coverage; physical validation of those capabilities is still pending. Full border-router outage behavior and radio conditions also need hardware testing. [Test lab and reproducible checks](tests/lab/README.md)

## Built on open source, with AI assistance

Matter Reflex was made possible by the work behind:

- **[ESPHome](https://esphome.io/)**, which provides the firmware framework, configuration, encrypted API and OTA updates.
- **[DavidvtWout's esphome-matter](https://github.com/DavidvtWout/esphome-matter)**, the community Matter component used by the ESPHome package.
- **[Home Assistant](https://www.home-assistant.io/)** and **[Python Matter Server](https://github.com/home-assistant-libs/python-matter-server)**, which provide the automation editor, device integration and controller access.
- **[ESP-IDF](https://github.com/espressif/esp-idf)**, **[ESP-Matter](https://github.com/espressif/esp-matter)**, **[the Matter SDK](https://github.com/project-chip/connectedhomeip)** and **[OpenThread](https://openthread.io/)**, which underpin the firmware and its Matter and Thread communication.
- **[Matter.js](https://github.com/matter-js/matter.js)** and **[ESP-EMU](https://github.com/espressif/esp-emulator)**, which make the simulated devices and firmware outage lab possible.

We used **ChatGPT's Codex** to help develop the code, test tooling and documentation.

## Try it, report a problem, or contribute

If you have compatible Matter devices, we'd love to hear how it works in your setup. Include your device models, software versions and a minimal automation when [reporting an issue](https://github.com/Tito1337/matter-reflex/issues). Contributions are welcome; the [contribution guide](CONTRIBUTING.md) explains the code and checks.

Matter Reflex is open source under the **[Mozilla Public License 2.0](LICENSE)**, which permits commercial use.
