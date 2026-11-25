"""Boot the real ESPHome image, recording status and saving flash across restarts."""

import asyncio
import json
import re
import signal
import sys
from pathlib import Path

RUN = Path("/run/lab")
STATUS = re.compile(
    rb"node=([0-9a-f]{16}) active=([01]) rules=([0-9a-f]*) executions=(\d+) peers=(\d+)/(\d+)"
)


def observe_radio(board, line):
    if match := re.search(rb"(router|parent)=([0-9a-fA-F]{16})", line):
        target = RUN / f"{board}-{match[1].decode()}"
        address = match[2].decode().lower()
        if board == "contact" and b"sleepy=1" in line:
            target = RUN / "contact-sleepy-parent"

        if not target.exists() or target.read_text() != address:
            temp = target.with_suffix(".new")
            temp.write_text(address)
            temp.replace(target)


class RadioLink(asyncio.DatagramProtocol):
    """Fan out raw radio frames across the three emulated transceivers."""

    def __init__(self, port, radios):
        self.port = port
        self.radios = radios

    def datagram_received(self, data, addr):
        for port, transport in self.radios.items():
            if port != self.port:
                transport.sendto(data, ("127.0.0.1", port))


async def run_host_router():
    process = await asyncio.create_subprocess_exec(
        "/esp-emu",
        "--chip",
        "esp32c6",
        "--firmware",
        "/run/lab/host-router.bin",
        "--elf",
        "/host-router.elf",
        "--net",
        "tap,ifname=harouter0",
        "--thread-sim",
        "bind:9003,peer:127.0.0.1:9103",
        "--console",
        "usb-jtag",
        "--log-color",
        "never",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    async def observe():
        with (RUN / "host-router.log").open("ab", buffering=0) as log:
            async for line in process.stdout:
                log.write(line)
                observe_radio("host", line)

    observer = asyncio.create_task(observe())
    try:
        while not (RUN / "stop-host-router").exists():
            if process.returncode is not None:
                raise RuntimeError("HA-host router exited unexpectedly")
            await asyncio.sleep(0.2)
        process.kill()
        await process.wait()
        await observer
        (RUN / "host-router").unlink(missing_ok=True)
        (RUN / "host-router-off").touch()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def host_router():
    while True:
        await run_host_router()
        while (RUN / "stop-host-router").exists():
            await asyncio.sleep(0.2)
        (RUN / "host-router-off").unlink()


async def thread_contact():
    process = await asyncio.create_subprocess_exec(
        "/esp-emu",
        "--chip",
        "esp32c6",
        "--firmware",
        "/run/lab/contact.bin",
        "--elf",
        "/contact.elf",
        "--net",
        "user,restrict=yes",
        "--thread-sim",
        "bind:9001,peer:127.0.0.1:9101",
        "--console",
        "usb-jtag",
        "--log-color",
        "never",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    async def forward():
        request = RUN / "contact-command"
        while True:
            if request.is_file():
                process.stdin.write(request.read_bytes() + b"\r\n")
                await process.stdin.drain()
                request.unlink()
            await asyncio.sleep(0.1)

    commands = asyncio.create_task(forward())
    try:
        with (RUN / "contact.log").open("ab", buffering=0) as log:
            async for line in process.stdout:
                log.write(line)
                observe_radio("contact", line)
                operational = re.search(
                    rb"Publishing [0-9a-fA-F]+-([0-9A-Fa-f]+)\._matter\._tcp", line
                )
                if operational and not (RUN / "contact-node").exists():
                    temp = RUN / "contact-node.new"
                    temp.write_text(str(int(operational[1], 16)))
                    temp.replace(RUN / "contact-node")
                if match := re.search(rb"Manual pairing code: ([0-9-]+)", line):
                    (RUN / "contact-code").write_text(
                        match[1].decode().replace("-", "")
                    )
                if match := re.search(
                    rb"Publishing ([0-9A-Fa-f]+)\._matterc\._udp", line
                ):
                    (RUN / "contact-instance").write_text(match[1].decode())
                if match := re.search(rb"contact=([ocps])", line):
                    (RUN / "contact-ack").write_text(match[1].decode())
        raise RuntimeError("Thread contact emulator stopped")
    finally:
        commands.cancel()
        if process.returncode is None:
            process.terminate()
            await process.wait()


async def main():
    args = [
        "/esp-emu",
        "--chip",
        "esp32c6",
        "--firmware",
        "/run/lab/reflex.bin",
        "--elf",
        "/reflex.elf",
        "--net",
        "tap,ifname=reflex0" if sys.argv[1] == "combined" else "tap,ifname=tap0",
        "--console",
        "usb-jtag",
        "--save-state",
        "--log-color",
        "never",
    ]
    if sys.argv[1] == "combined":
        args += ["--thread-sim", "bind:9002,peer:127.0.0.1:9102"]
    while True:
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )

        async def observe(process):
            receptions = {}
            with (RUN / "board.log").open("ab", buffering=0) as log:
                async for line in process.stdout:
                    log.write(line)
                    observe_radio("reflex", line)
                    if match := re.search(rb"Manual pairing code: ([0-9-]+)", line):
                        (RUN / "pairing-code").write_text(
                            match[1].decode().replace("-", "")
                        )
                    if match := re.search(rb"peer_rx=([0-9a-f]+) tick=(\d+)", line):
                        node = str(int(match[1], 16))
                        receptions[node] = max(receptions.get(node, 0), int(match[2]))
                    if match := STATUS.search(line):
                        node = int(match[1], 16)
                        state = {
                            "node": str(node),
                            "fabrics": int(bool(node)),
                            "active": match[2] == b"1",
                            "rules": match[3].decode(),
                            "executions": int(match[4]),
                            "received": receptions,
                            "ready": int(match[5]),
                            "required": int(match[6]),
                        }
                        temp = RUN / "status.new"
                        temp.write_text(json.dumps(state))
                        temp.replace(RUN / "status.json")

        observer = asyncio.create_task(observe(process))
        while not (RUN / "power-off").exists():
            if process.returncode is not None:
                raise RuntimeError(f"ESPHome emulator exited with {process.returncode}")
            await asyncio.sleep(0.2)
        process.send_signal(signal.SIGINT)
        await asyncio.wait_for(process.wait(), 30)
        await observer
        (RUN / "status.json").unlink(missing_ok=True)
        (RUN / "powered-off").touch()
        while (RUN / "power-off").exists():
            await asyncio.sleep(0.2)
        (RUN / "powered-off").unlink()


async def run():
    if sys.argv[1] == "combined":
        loop = asyncio.get_running_loop()
        radios = {}
        for port in (9001, 9002, 9003):
            radios[port], _ = await loop.create_datagram_endpoint(
                lambda port=port: RadioLink(port, radios),
                local_addr=("127.0.0.1", port + 100),
            )

        async def boot_contact():
            async with asyncio.timeout(120):
                while not (RUN / "host-router").exists():
                    await asyncio.sleep(0.2)
            await thread_contact()

        async def boot_reflex():
            # Let the input attach to the HA-host radio first.
            async with asyncio.timeout(180):
                while not (
                    (RUN / "contact-parent").exists() and (RUN / "host-router").exists()
                ):
                    await asyncio.sleep(0.2)
            if (RUN / "contact-parent").read_text() != (
                RUN / "host-router"
            ).read_text():
                raise RuntimeError("Thread input did not attach to the HA host")
            while not (RUN / "host-route-ready").exists():
                await asyncio.sleep(0.2)
            await main()

        await asyncio.gather(host_router(), boot_contact(), boot_reflex())
    else:
        await main()


asyncio.run(run())
