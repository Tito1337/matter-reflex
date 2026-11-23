"""Manage one disposable Thread mesh and observe the emulated board over USB."""

import csv
import json
import os
import re
import secrets
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from lab import PODMAN, eventually, podman

ROOT = Path(__file__).resolve().parents[3]


def boot_milestones(lines):
    # The pinned SDK timestamps both CHIP and OpenThread logs in guest milliseconds.
    stages = {}
    subscriptions = {}
    route_drops = 0
    for line in lines:
        stamp = re.match(rb"[IWE]\s*\(\s*(\d+)\)", line)
        if not stamp:
            continue
        ms = int(stamp[1])
        if b"Returned from app_main" in line:
            stages.setdefault("app_main_return", ms)
        elif b"Role detached ->" in line and b"disabled" not in line:
            stages.setdefault("attached", ms)
        elif b"Msg TX" in line and b"(SecureChannel:CASE_Sigma1)" in line:
            stages.setdefault("first_sigma1", ms)
        elif b"Failed to find valid route for:" in line:
            route_drops += 1
        elif b"Msg RX" in line and b"(IM:SubscribeResponse)" in line:
            sender = re.search(rb"Msg RX from (\S+)", line)
            if sender:
                subscriptions.setdefault(sender[1], ms)
    if len(subscriptions) == 2:
        stages["both_subscribe_responses"] = max(subscriptions.values())
    return {**stages, "route_drops": route_drops}


class EmulatedBoard:
    def __init__(self, cleanup, start, prefix, ha):
        self.run = Path(cleanup.enter_context(TemporaryDirectory(prefix="reflex-emu-")))
        self.key = secrets.token_hex(32)
        with os.fdopen(
            os.open(self.run / "http-key", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
            "w",
        ) as f:
            f.write(self.key)
        self.name = prefix + "mesh"
        firmware = str(ROOT / "firmware/build") + ":/firmware:ro,z"
        scripts = str(ROOT / "tests/lab/c6") + ":/recipe:ro,z"
        scratch = str(self.run) + ":/run/lab:z"
        podman(
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            firmware,
            "-v",
            scratch,
            "-v",
            str(ROOT / "tools/factory.py") + ":/generate.py:ro,z",
            os.environ["REFLEX_LAB_MATTER_IMAGE"],
            "bash",
            "-ec",
            "python /generate.py /run/lab && cd /firmware && "
            "python -m esptool --chip esp32c6 merge_bin -o /run/lab/reflex.bin "
            "@flash_args 0x1a000 /run/lab/factory/factory_partition.bin",
            timeout=120,
        )
        options = ["--device", "/dev/net/tun", "--cap-add", "NET_ADMIN", "-v", scripts]
        start(
            "tap",
            ha,
            [
                *options,
                "--cap-add",
                "NET_RAW",
                "--sysctl",
                "net.ipv6.conf.default.accept_ra=2",
                "--sysctl",
                "net.ipv6.conf.default.accept_ra_rt_info_max_plen=128",
                "--sysctl",
                "net.ipv6.conf.default.autoconf=1",
                "--entrypoint",
                "python",
            ],
            ["/recipe/tap.py"],
        )
        eventually(
            lambda: "tap0" in podman("exec", prefix + "tap", "ip", "link", "show"),
            "isolated TAP interface",
        )
        start(
            "mesh",
            os.environ["REFLEX_LAB_IDF_IMAGE"],
            [
                *options,
                "-v",
                firmware,
                "-v",
                scratch,
                "-v",
                str(ROOT / "firmware/build/reflex-lab") + ":/artifacts:ro,z",
                "--entrypoint",
                "python3",
            ],
            ["/recipe/mesh.py"],
            network="container:" + prefix + "tap",
        )

        def reachable():
            address = self.run / "address"
            if not address.exists():
                return False
            return (
                subprocess.run(
                    [
                        *PODMAN,
                        "exec",
                        prefix + "tap",
                        "ping",
                        "-6",
                        "-c",
                        "1",
                        "-W",
                        "8",
                        address.read_text(),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                    check=False,
                ).returncode
                == 0
            )

        eventually(reachable, "published and routable Thread address", timeout=150)
        # The TAP backend batches idle Wi-Fi traffic. A link-local echo keeps
        # the simulated backbone responsive without crossing the Thread mesh.
        route = podman(
            "exec",
            prefix + "tap",
            "ip",
            "-6",
            "route",
            "get",
            (self.run / "address").read_text(),
        ).split()
        router = route[route.index("via") + 1]
        podman(
            "exec",
            "-d",
            prefix + "tap",
            "sh",
            "-c",
            'exec ping -6 -I tap0 -i 0.05 "$1" >/dev/null',
            "sh",
            router,
        )
        print(
            "Reflex firmware reached through the virtual Thread border router.",
            flush=True,
        )

    def wait(self, description, predicate, timeout=180):
        def ready():
            if podman("inspect", "--format", "{{.State.Running}}", self.name) != "true":
                raise RuntimeError(
                    "Thread emulator exited while waiting for " + description
                )
            path = self.run / "status.json"
            if path.exists():
                state = json.loads(path.read_text())
                if predicate(state):
                    return state
            return None

        return eventually(ready, description, timeout=timeout)

    def power_off(self):
        (self.run / "power-off").touch()
        eventually(
            (self.run / "powered-off").exists,
            "ESP emulator to exit and save flash",
            timeout=35,
        )

    def power_on(self):
        (self.run / "power-off").unlink()

    def pairing_code(self):
        self.wait(
            "an attached uncommissioned Thread device",
            lambda s: s["attached"] and not s["fabrics"],
        )
        with (self.run / "factory/onboarding_codes.csv").open() as f:
            return next(csv.DictReader(f))["manualcode"]
