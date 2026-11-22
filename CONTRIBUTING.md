# Contributing to Matter Reflex

The Python compiler decides whether Home Assistant can delegate an entire automation. The C++ firmware must then reproduce its actions using the information available during an outage. A change on either side can affect how the other behaves.

Write documentation, source comments, command-line messages and examples in English. Home Assistant interface translations belong in `custom_components/matter_reflex/translations/`.

## Check staged changes for secrets

Keep household Thread datasets, Wi-Fi credentials and API keys in local provisioning files. Use `!secret` references in shared ESPHome YAML. Run `pre-commit install` once per clone to enable the Gitleaks hook. With the Gitleaks CLI installed, you can also check staged changes directly.

```sh
gitleaks git --pre-commit --staged --redact=100
```

CI scans the commit history on pushes and pull requests. It reports the file, line, rule and commit. If a live credential reached a remote, rotate it even if a later commit removed it.

## Start with the ESPHome example

From a Linux checkout with Bash and rootless Podman, validate the [example node](examples/extend-node.yaml) and its downstream text sensor. The helper pulls the pinned toolchain on first use, generates disposable credentials and cleans up its temporary build. You can run it without a board or access to a household Thread network.

```sh
tools/check-esphome
tools/check-esphome compile extend-node
```

To test a XIAO layout, replace `extend-node` with `xiao-esp32c6-ota` or `xiao-esp32c6-existing-ota`. For a build you intend to flash, use your own secrets and the [installation guide](examples/README.md). To add Reflex to a project of your own, follow the [component guide](docs/esphome-component.md).

## Where to make a change

| Change | Start here |
| --- | --- |
| YAML settings, diagnostics or callbacks | [ESPHome schema and code generation](components/matter_reflex/__init__.py) and [C++ adapter](components/matter_reflex/matter_reflex.cpp) |
| New board or a component consuming Reflex events | [Board-independent package](examples/reflex.yaml) and [downstream text sensor](examples/demo_components/reflex_last_result/) |
| Gesture handling and command results | [Rule engine](components/matter_reflex/reflex_engine.cpp), [peer connections](components/matter_reflex/reflex_peers.cpp) and [Matter runtime](components/matter_reflex/reflex_runtime.cpp) |
| Supported HA automations, permissions or lease renewal | [Compiler](custom_components/matter_reflex/compiler.py), [ACL handling](custom_components/matter_reflex/acl.py) and [HA runtime](custom_components/matter_reflex/runtime.py) |
| Rule format shared by Python and C++ | [Wire format](custom_components/matter_reflex/wire.py) and [contract tests](tests/contract/) |

Send feature and bug-fix pull requests to `main`. ESPHome is the firmware development path. To reproduce the last standalone baseline in a separate checkout, run `git worktree add --detach ../matter-reflex-standalone standalone-v1.3.0`.

The standalone build remains available during migration. Before retiring it, record a fresh XIAO commissioning, an encrypted OTA update over Thread that preserves the same node and rules, and a physical button controlling its target during a Matter Server outage on that installation. [Hardware procedure and observations](examples/README.md#thread-ota)

## Check the integration and shared engine

From a Linux checkout with rootless Podman, build the Home Assistant test image first. You can run the software tests without a USB board, a Home Assistant instance, or Thread credentials.

```sh
podman --cgroup-manager=cgroupfs build -f tools/Containerfile.ha -t localhost/matter-reflex-ha:2026.11.1 tools
./dev ha python -m pytest -q
./dev ha ruff check custom_components/matter_reflex tests tools/board_console.py
```

Run the contract test in Bash to check that the two components agree. The first command in the pipeline uses the integration’s Python compiler to compile the README example and three additional actions. The second runs the C++ engine on your development machine to decode and execute those rules. The test covers a 64-bit Matter identifier, a negative brightness step, a shade set to 75%, and a toggle based on the target’s observed state.

```bash
set -o pipefail
./dev ha python - < tests/contract/encode.py | ./dev idf bash tools/test-engine
```

Run `./dev test-sensors` to exercise the README contact and motion automations plus leak and environmental rules on an offline Matter.js network. This uses the HA test image and a host C++17 compiler with static linking, falling back to the pinned IDF image if g++ is absent. The host engine processes subscription reports with a deterministic lease clock. The simulated peers execute the resulting commands.

For independent virtual buttons, lamps and blinds, the [Matter outage lab](tests/lab/README.md) includes an offline protocol check, a disposable Home Assistant fabric, and an outage scenario for a commissioned XIAO.

The contract run also checks the firmware lease deadline. Run `./dev test-lease` to commission a pair of simulated nodes with the pinned Matter Server SDK and verify that each named action arrives in a single encrypted UDP datagram. Podman pulls the test image on first use. The test runs without network access or household credentials.

If you change the firmware or SDK, also build the application for the microcontroller. Linking checks CHIP API compatibility and size limits that the host-side engine test does not cover.

```sh
./dev matter idf.py -C firmware build
./dev matter idf.py -C firmware size
```

### Choose a test tier

| When | What runs | How to run it |
| --- | --- | --- |
| Pull requests and pushes to `main` | One Linux job runs pytest, lint, the host C++ engine, Matter lease and sensor contracts, and the in-memory peer smoke test. New commits cancel superseded runs on the same PR. | [Project checks](.github/workflows/checks.yml) |
| HA commissioning or controller recovery changes | A disposable Home Assistant commissions both peers and exercises their automations across a Matter Server restart. | `./dev lab smoke-ha` after building the HA test image, or manually select the checkbox in **Project checks**. |
| Firmware ownership, Thread or takeover changes | The actual C6 firmware runs on virtual Thread. Choose direct, fallback, advanced or all three. | `./dev lab takeover --mode direct` or the manual [ESP32-C6 firmware workflow](.github/workflows/firmware.yml). |
| ESPHome build or packaging changes | Compile one OTA profile, the extension example, or all three. | `tools/check-esphome compile extend-node` or the manual [ESPHome workflow](.github/workflows/esphome.yml). |

For local builds and the hardware verification sequence see the [XIAO profiles](examples/README.md).

## Adding a Matter target

1. Record the device type, cluster, accepted commands, and capabilities of the Home Assistant entity. A single plug may expose both a switch for its power output and several configuration settings.
2. Describe the expected behavior when the target’s state is known, unknown, or stale. For example, a toggle requires a recent on/off state.
3. Test that the compiler accepts a supported configuration and rejects an unsupported automation in full. Check the required permissions on each endpoint and compatibility with existing rule images.
4. If the rule format or behavior changes, extend the [contract test](tests/contract) and document what happens on upgrade. In the pull request, list the tests you ran on hardware and the versions you used.

## Reporting a problem

Include the versions of Home Assistant Core, Matter Server, the integration, and the firmware. Give the source and target device models, describe the gesture, and explain what you expected and what actually happened. A minimal automation with anonymized entity IDs often helps others reproduce the problem. For an outage, include the Reflex mode during the press and the change in the execution counter. [Open an issue](https://github.com/Tito1337/matter-reflex/issues)

Keep flash dumps, HMAC keys, Thread datasets, and pairing codes out of issues and commits. They can expose private information. Redact device identifiers and household names from log excerpts as well. The [troubleshooting guide](docs/troubleshooting.md) lists the observations to include.
