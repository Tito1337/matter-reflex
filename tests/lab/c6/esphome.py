"""Run the ESPHome firmware on the existing lab's isolated IPv6 backbone."""

import os
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from lab import eventually, podman

from c6 import ROOT, EmulatedBoard, keep_backbone_responsive


class NetworkBoard(EmulatedBoard):
    def __init__(self, cleanup, start, prefix, ha, transport):
        fixture = ROOT / "examples/.esphome/network-lab"
        self.run = Path(
            cleanup.enter_context(TemporaryDirectory(prefix="run-", dir=fixture))
        )

        def save_failure(error_type, _error, _traceback):
            if error_type is not None:
                for name in (
                    "board.log",
                    "contact.log",
                    "host-router.log",
                    "status.json",
                ):
                    log = self.run / name
                    if log.exists():
                        shutil.copyfile(log, fixture / f"{transport}-failed-{name}")

        cleanup.push(save_failure)
        self.name = prefix + "mesh"
        self.tap = prefix + "tap"
        self.api_key = (fixture / f"{transport}-api-key").read_text()
        self.api_host = f"lab-reflex-{transport}.local"
        name = "lab-reflex-" + transport
        build = fixture / ".esphome/build" / name / "build"
        shutil.copyfile(build / "firmware.factory.bin", self.run / "reflex.bin")
        options = [
            "--device",
            "/dev/net/tun",
            "--cap-add",
            "NET_ADMIN",
            "-v",
            f"{ROOT / 'tests/lab/c6'}:/recipe:ro,z",
        ]
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
                "--entrypoint",
                "python",
            ],
            ["/recipe/tap.py", transport],
        )
        eventually(
            lambda: (
                "fd42:5246:1::1" in podman("exec", prefix + "tap", "ip", "-6", "addr")
            ),
            "isolated Wi-Fi backbone",
        )
        peer_options = []
        if transport == "combined":
            contact = fixture / ".esphome/build/lab-thread-contact/build"
            shutil.copyfile(contact / "firmware.factory.bin", self.run / "contact.bin")
            router = fixture / ".esphome/build/lab-ha-router/build"
            shutil.copyfile(
                router / "firmware.factory.bin", self.run / "host-router.bin"
            )
            peer_options = [
                "-v",
                f"{router / 'lab-ha-router.elf'}:/host-router.elf:ro,z",
                "-v",
                f"{contact / 'lab-thread-contact.elf'}:/contact.elf:ro,z",
            ]
        start(
            "mesh",
            os.environ["REFLEX_LAB_IDF_IMAGE"],
            [
                *options,
                *peer_options,
                "-v",
                f"{self.run}:/run/lab:z",
                "-v",
                f"{build / (name + '.elf')}:/reflex.elf:ro,z",
                "-v",
                f"{ROOT / 'firmware/build/reflex-lab/esp-emu'}:/esp-emu:ro,z",
                "--entrypoint",
                "python3",
            ],
            ["/recipe/network.py", transport],
            network="container:" + prefix + "tap",
        )

    def pairing_code(self):
        path = self.run / "pairing-code"
        return eventually(
            lambda: path.read_text() if path.exists() else None,
            "ESPHome commissioning code",
            timeout=240,
        )

    def contact_code(self):
        self.wait_parent("host", sleepy=False)
        path = self.run / "contact-code"
        instance = self.run / "contact-instance"
        code = eventually(
            lambda: path.read_text() if path.exists() and instance.exists() else None,
            "Thread contact commissioning advertisement",
            timeout=180,
        )
        address = podman(
            "exec",
            self.tap,
            "python",
            "/recipe/thread_probe.py",
            instance.read_text(),
            timeout=150,
        )
        keep_backbone_responsive(self.tap, address)
        # Set a known closed state in both the sensor and its Matter attribute
        # before commissioning. ESPHome may suppress an initial state callback.
        self.set_contact("o")
        self.set_contact("c")
        (self.run / "host-route-ready").touch()
        return code

    def wait_parent(self, router, *, sleepy=True):
        expected = self.run / f"{router}-router"
        parent = self.run / ("contact-sleepy-parent" if sleepy else "contact-parent")
        eventually(
            lambda: (
                expected.exists()
                and parent.exists()
                and parent.read_text() == expected.read_text()
            ),
            f"Thread input attached to {router} router (sleepy={sleepy})",
            timeout=300,
        )

    def stop_host_router(self, *, sleepy):
        self.wait_parent("host", sleepy=sleepy)
        host = (self.run / "host-router").read_text()
        reflex = self.run / "reflex-router"
        (self.run / "stop-host-router").touch()
        eventually((self.run / "host-router-off").exists, "HA-host router powered off")
        self.wait_parent("reflex", sleepy=sleepy)
        if reflex.read_text() == host:
            raise RuntimeError("Both emulated parents reported the same Thread address")
        print(
            f"Verified Thread input reparented from {host} to {reflex.read_text()}.",
            flush=True,
        )

    def wait_thread_traffic(self):
        peer = (self.run / "contact-node").read_text()
        before = self.wait("Reflex telemetry", lambda s: s["active"])["received"].get(
            peer, 0
        )
        self.wait(
            "fresh authenticated Thread traffic and established subscriptions",
            lambda s: (
                s["received"].get(peer, 0) > before and s["ready"] == s["required"] == 3
            ),
            timeout=300,
        )

    def restart_host_router(self):
        (self.run / "stop-host-router").unlink(missing_ok=True)

    def set_contact(self, value):
        ack = self.run / "contact-ack"
        ack.unlink(missing_ok=True)
        request = self.run / "contact-command"
        temp = self.run / "contact-command.new"
        temp.write_text(value)
        temp.replace(request)
        eventually(
            lambda: ack.is_file() and ack.read_text() == value,
            "Thread contact USB acknowledgment",
            timeout=30,
        )
