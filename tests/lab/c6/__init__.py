"""Manage one disposable Thread mesh and observe the emulated board over USB."""

import csv
import json
import os
import secrets
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from lab import PODMAN, eventually, podman

ROOT = Path(__file__).resolve().parents[3]


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

    def pairing_code(self):
        self.wait(
            "an attached uncommissioned Thread device",
            lambda s: s["attached"] and not s["fabrics"],
        )
        with (self.run / "factory/onboarding_codes.csv").open() as f:
            return next(csv.DictReader(f))["manualcode"]
