"""Build the selected checkout profile with disposable lab credentials."""

import base64
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

from dataset import new_dataset

ROOT = Path(__file__).resolve().parents[3]
PROFILE = sys.argv[1]
if PROFILE not in {"wifi", "combined"}:
    raise SystemExit("Expected wifi or combined")

os.umask(0o077)
fixture = ROOT / "examples/.esphome/network-lab"
fixture.mkdir(parents=True, exist_ok=True)
# The package resolves local component sources relative to the device YAML.
lines = [
    "packages:",
    "  reflex: !include ../../xiao-networks.yaml",
    "esphome:",
    f"  name: lab-reflex-{PROFILE}",
    "  includes: [lab_status.h, lab_radio.h]"
    if PROFILE == "combined"
    else "  includes: [lab_status.h]",
    "substitutions:",
    "  reflex_source: ../../../components",
    "  reflex_partitions: ../../partitions-ota.csv",
    "  reflex_api_key: !secret reflex_api_key",
    "  reflex_wifi_ssid: !secret wifi_ssid",
    "  reflex_wifi_password: !secret wifi_password",
]
if PROFILE == "combined":
    lines.append("  reflex_thread_tlv: !secret thread_tlv")
    shutil.copyfile(Path(__file__).with_name("lab_radio.h"), fixture / "lab_radio.h")
lines.extend(
    [
        "interval:",
        "  - interval: 1s",
        "    then:",
        "      - lambda: lab_report_status();",
    ]
)
if PROFILE == "combined":
    lines.append("      - lambda: lab_report_radio();")
(fixture / f"{PROFILE}.yaml").write_text("\n".join(lines) + "\n")
key = base64.b64encode(secrets.token_bytes(32)).decode()
(fixture / f"{PROFILE}-api-key").write_text(key)
(fixture / "secrets.yaml").write_text(
    f"reflex_api_key: '{key}'\n"
    "wifi_ssid: 'myssid'\nwifi_password: 'mypassword'\n"
    f"thread_tlv: '{new_dataset()}'\n"
)
shutil.copyfile(Path(__file__).with_name("lab_status.h"), fixture / "lab_status.h")
subprocess.run(
    [
        ROOT / "dev",
        "esphome",
        "compile",
        (fixture / f"{PROFILE}.yaml").relative_to(ROOT),
    ],
    cwd=ROOT,
    check=True,
)

if PROFILE == "combined":
    for filename in (
        "lab_contact.h",
        "thread-contact.yaml",
        "lab_router_mac.h",
        "ha-router.yaml",
    ):
        shutil.copyfile(Path(__file__).with_name(filename), fixture / filename)
    for device in ("ha-router.yaml", "thread-contact.yaml"):
        subprocess.run(
            [ROOT / "dev", "esphome", "compile", (fixture / device).relative_to(ROOT)],
            cwd=ROOT,
            check=True,
        )
