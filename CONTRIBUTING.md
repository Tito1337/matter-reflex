# Contributing to Matter Reflex

The Python compiler decides whether Home Assistant can delegate an entire automation. The C++ firmware must then reproduce its actions using the information available during an outage. A change on either side can affect how the other behaves.

Write documentation, source comments, command-line messages and examples in English. Home Assistant interface translations belong in `custom_components/matter_reflex/translations/`.

## Checking a change

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

If you change the firmware or SDK, also build the application for the microcontroller. Linking checks CHIP API compatibility and size limits that the host-side engine test does not cover.

```sh
./dev matter idf.py -C firmware build
./dev matter idf.py -C firmware size
```

The [software checks](.github/workflows/checks.yml) run on pushes and pull requests. The [ESP32-C6 build](.github/workflows/firmware.yml) runs when the firmware, its launcher, or its workflow changes. You can also run either workflow manually on GitHub.

## Adding a Matter target

1. Record the device type, cluster, accepted commands, and capabilities of the Home Assistant entity. A single plug may expose both a switch for its power output and several configuration settings.
2. Describe the expected behavior when the target’s state is known, unknown, or stale. For example, a toggle requires a recent on/off state.
3. Test that the compiler accepts a supported configuration and rejects an unsupported automation in full. Check the required permissions on each endpoint and compatibility with existing rule images.
4. If the rule format or behavior changes, extend the [contract test](tests/contract) and document what happens on upgrade. In the pull request, list the tests you ran on hardware and the versions you used.

## Reporting a problem

Include the versions of Home Assistant Core, Matter Server, the integration, and the firmware. Give the source and target device models, describe the gesture, and explain what you expected and what actually happened. A minimal automation with anonymized entity IDs often helps others reproduce the problem. For an outage, include the Reflex mode during the press and the change in the execution counter. [Open an issue](https://github.com/Tito1337/matter-reflex/issues)

Keep flash dumps, HMAC keys, Thread datasets, and pairing codes out of issues and commits. They can expose private information. Redact device identifiers and household names from log excerpts as well. The [troubleshooting guide](docs/troubleshooting.md) lists the observations to include.
