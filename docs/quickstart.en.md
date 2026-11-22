# Matter Reflex for Home Assistant

Keep Matter sensors and buttons working when Home Assistant or its Matter Server goes offline. The integration compiles labeled automations. A Seeed Studio XIAO ESP32-C6 joins the same Matter fabric over Thread, stores the rules, and sends direct rules to their targets during normal operation. For fallback rules, the ESP takes over when the ten-second lease expires.

## What you need

- Home Assistant with Matter, a working Thread network and border router, and access to the active Thread dataset.
- A XIAO ESP32-C6, a USB data cable and ESPHome Device Builder or a Linux checkout with rootless Podman. The build uses Espressif’s development attestation profile.

## First automation

1. Build, provision and commission the board using the [ESPHome XIAO installation guide](../examples/README.md).
2. Copy [the integration](../custom_components/matter_reflex) to `config/custom_components/matter_reflex`, check the HA configuration and restart Core. Under **Settings → Devices & services**, add **Matter Reflex** and select the board.
3. Use the [MYGGBETT door or MYGGSPRAY motion example](../README.md#your-first-automation) with your own binary sensor and light entity IDs. Apply the **reflex** label to the enabled automation.
4. For ESP-first operation, add the **Reflex mode** condition to the automation, choose the ESP and **Direct reflex**, and save. Check the automation’s Activity for reported ESP runs. [Modes and advanced handlers](../README.md#direct-and-fallback-reflexes)
5. Check both the synchronization sensor and **Repairs**. The sensor confirms the accepted rule image reached the board. Repairs lists any automation excluded from that image.

The controller supports explicit Matter light, plug and shade targets. For example, it can switch a plug, adjust a dimmable light or send a shade to a fixed position. It stores up to 32 trigger–target associations. Conditions other than the Reflex handler selection, templates, area targets, color settings and complex action blocks require HA. To test a door rule on hardware, start with the door closed and the lamp off. Check that all required peers are ready. Stop renewals by taking HA or its Matter Server offline and wait for the takeover LED or the USB log message `Lease state: active`. Open the door once. The light should turn on and **Executions** should increase by one. Leave the door open through recovery and check for standby after renewal. The [test results](../README.md#simulator-tested-support) distinguish simulated capabilities from physical observations.

[Supported actions](../README.md#supported-actions) · [Contributing and local checks](../CONTRIBUTING.md) · [MPL-2.0](../LICENSE)
