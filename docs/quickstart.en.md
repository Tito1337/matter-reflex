# Matter Reflex for Home Assistant

Keep a Matter button working when Home Assistant or its Matter Server goes offline. The integration compiles labeled automations. A Seeed Studio XIAO ESP32-C6 joins the same Matter fabric over Thread, stores the rules, and sends commands directly to the targets once the ten-second lease expires.

## What you need

- Home Assistant with Matter, a working Thread network and border router, and access to the active Thread dataset.
- A XIAO ESP32-C6, a USB data cable and ESPHome Device Builder or a Linux checkout with rootless Podman. The build uses Espressif’s development attestation profile.

## First automation

1. Build, provision and commission the board using the [ESPHome XIAO installation guide](../examples/README.md).
2. Copy [the integration](../custom_components/matter_reflex) to `config/custom_components/matter_reflex`, check the HA configuration and restart Core. Under **Settings → Devices & services**, add **Matter Reflex** and select the board.
3. Use the [double-press example](../README.md#your-first-automation) with your own event and light entity IDs. Apply the **reflex** label to the enabled automation.
4. Check both the synchronization sensor and **Repairs**. The sensor confirms the accepted rule image reached the board. Repairs lists any automation excluded from that image.

The controller supports explicit Matter light, plug and shade targets. For example, it can switch a plug, adjust a dimmable light or send a shade to a fixed position. It stores up to 32 gesture–target associations. Conditions, templates, area targets, color settings and complex action blocks require HA. For an outage test, observe a physical target and use the XIAO takeover LED or ESPHome USB log to confirm local control during the press and standby after recovery.

[Supported actions](../README.md#supported-actions) · [Contributing and local checks](../CONTRIBUTING.md) · [MPL-2.0](../LICENSE)
