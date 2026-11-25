# Add Reflex to an existing ESPHome node

Use the board-independent Reflex package when your ESPHome device already has a job of its own. The [working example](../examples/extend-node.yaml) exposes chip temperature on Matter endpoint 3, logs failed Reflex commands, and publishes the last command result through an external text sensor. For a dedicated XIAO with a takeover LED, start with the [XIAO installation guide](../examples/README.md).

## Add the package

The example below uses an ESP32-C6 running ESP-IDF and OpenThread. For a fresh XIAO on Wi-Fi, Thread, or both, use the [credential-selected configuration](wifi-support.md). The tested board is the Seeed XIAO ESP32-C6. Your configuration supplies the board, name, Thread dataset, and Matter product ID. Check that you can dedicate endpoints 1 and 2 to Reflex. Endpoint 0 is the Matter root. Keep endpoint numbers stable for devices that have already been commissioned.

Merge the following into your existing YAML. The management package lets Home Assistant transfer rules through the encrypted ESPHome API over Thread. If your node already declares an API encryption key, use that same key here. Create local secrets for the active Thread dataset and API key before the first USB installation. [Obtaining the dataset](../firmware/README.md#join-the-thread-network)

```yaml
packages:
  reflex: github://Tito1337/matter-reflex/examples/reflex.yaml@70502fffa47de275665efb7484baf1098e541c8b
  management: github://Tito1337/matter-reflex/examples/encrypted-api.yaml@70502fffa47de275665efb7484baf1098e541c8b

substitutions:
  reflex_api_key: !secret reflex_api_key

openthread:
  tlv: !secret thread_tlv

matter:
  product_id: 0x8000
```

The package loads the pinned community Matter component and the Reflex external component. If you already import either from Git, consolidate those imports so your node uses one version of each. The package enables IPv6 and leaves your GPIOs, sensors, radio settings and OTA layout under your control. The Home Assistant integration currently discovers the device using development vendor ID `0xFFF1` and the Matter product name `Matter Reflex`. Reflex sets that product name for the whole node. Commission the node on the same Home Assistant Matter fabric as the source sensors, buttons and targets. [XIAO commissioning and transfer setup](../examples/README.md)

The Reflex integration automatically disables the unused Identify buttons on endpoints 1 and 2 and the legacy outlet switch on endpoint 2 in Home Assistant. These are internal protocol endpoints: the switch is not a Reflex enable/disable control, and the buttons have no identification effect configured. Their Matter clusters remain available for commissioning and compatibility with older controllers. Entities on your own endpoints and Reflex diagnostics are unaffected.

For development from a checkout, use `!include reflex.yaml` and set `reflex_source: ../components` in a YAML file beside the package in `examples/`. The [complete configuration](../examples/extend-node.yaml) uses local paths for Reflex and its example consumer. The [ESPHome external components documentation](https://esphome.io/components/external_components/) describes the directory layout and Git source syntax.

## React to takeover or a command result

Add the entries you need under `matter_reflex`. For example, this logs a line on takeover and another for each target command.

```yaml
matter_reflex:
  active:
    id: reflex_active
    internal: true
    on_press:
      - logger.log: "Reflex is handling button events"
  on_rule_result:
    - logger.log:
        format: "Rule %08lx, source event %llu, status 0x%03x"
        args: [rule_id, event_number, status]
```

| Option | What your configuration receives |
| --- | --- |
| `active` | A binary sensor. Use `on_press` for takeover and `on_release` for handback to Home Assistant. |
| `ready_peers`, `required_peers` | Diagnostic sensors for the subscribed and required peer counts. One node can serve several rules. |
| `on_rule_result` | One callback per target command attempt. A gesture that targets two lamps can produce two results with the same source event number. |
| `api_rules` | Enables rule exchange with an encrypted native API and `api.reboot_timeout: 0s`. The management package provides the configuration and API action. |

The callback supplies a 32-bit rule ID, a 64-bit Matter source event number and a 16-bit status. Zero indicates success. Local errors include `0x100` for a missing session, `0x101` for an unknown target state and `0x102` for a transport error. Button results carry the originating node’s Matter event number. Sensor attribute reports have no event number and use zero in this field. [Troubleshooting](troubleshooting.md)

## Subscribe from your own external component

The [example text sensor](../examples/demo_components/reflex_last_result/) is a complete downstream component. Its Python platform declares a dependency on `matter_reflex`, accepts a `reflex_id` using `cv.use_id(ReflexComponent)`, and passes that instance to its C++ sensor during ESPHome setup. In C++, include `esphome/components/matter_reflex/matter_reflex.h` and register a listener with this signature.

```cpp
parent->add_on_rule_result_callback(
    [this](uint32_t rule_id, uint64_t event_number, uint16_t status) {
      // Update this component's state here.
    });
```

Listeners and the YAML automation run on the ESPHome main loop. Keep callbacks short and capture objects that live for the lifetime of the node. Multiple components can subscribe. The Home Assistant integration still compiles rules, manages Matter permissions and renews the lease. Your component can consume results without managing any of those.

Run `tools/check-esphome` from the repository root to validate the complete example with disposable credentials. Run `tools/check-esphome compile extend-node` to build the firmware. [Contributor setup](../CONTRIBUTING.md)
