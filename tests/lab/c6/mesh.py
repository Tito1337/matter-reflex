"""Boot the upstream border router and the unmodified Reflex image on one virtual radio link."""

import asyncio
import json
import re
import signal
from ipaddress import IPv6Address
from pathlib import Path

ARTIFACTS = Path("/artifacts")
RUN = Path("/run/lab")
STATUS = re.compile(
    rb"thread=(attached|detached) fabrics=(\d+) node=([0-9a-f]{16}) active=([01]) rules=([0-9a-f]*) executions=(\d+)"
)


async def main():
    loop = asyncio.get_running_loop()
    dataset = loop.create_future()
    booted = asyncio.Event()
    tasks = []
    processes = []

    async def boot(name, image, elf, network, radio, console):
        process = await asyncio.create_subprocess_exec(
            str(ARTIFACTS / "esp-emu"),
            "--chip",
            "esp32c6",
            "--firmware",
            image,
            "--elf",
            elf,
            "--net",
            network,
            "--thread-sim",
            radio,
            "--console",
            console,
            *(["--save-state"] if name == "board" else []),
            "--log-color",
            "never",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        processes.append(process)

        async def observe():
            with (RUN / (name + ".log")).open("ab", buffering=0) as log:
                async for line in process.stdout:
                    log.write(line)
                    if name == "router":
                        if address := re.search(
                            rb"(?:Set addr of host to|Add a new host, addr) ([A-Fa-f0-9:]+)",
                            line,
                        ):
                            (RUN / "address").write_text(
                                str(IPv6Address(address[1].decode()))
                            )
                        match = re.search(
                            rb"(?<![a-f0-9])([a-f0-9]{100,})(?![a-f0-9])",
                            line,
                            re.IGNORECASE,
                        )
                        if match and not dataset.done():
                            dataset.set_result(match[1])
                    elif b"Returned from app_main" in line:
                        booted.set()
                    elif match := STATUS.search(line):
                        state = {
                            "attached": match[1] == b"attached",
                            "fabrics": int(match[2]),
                            "node": str(int(match[3], 16)),
                            "active": match[4] == b"1",
                            "rules": match[5].decode(),
                            "executions": int(match[6]),
                        }
                        temp = RUN / "status.new"
                        temp.write_text(json.dumps(state))
                        temp.replace(RUN / "status.json")

        tasks.append(asyncio.create_task(observe()))
        return process

    async def send(process, message):
        process.stdin.write(message + b"\r\n")
        await process.stdin.drain()

    try:
        router = await boot(
            "router",
            "/artifacts/router.bin",
            "/artifacts/router/build/esp_ot_br.elf",
            "tap,ifname=tap0",
            "bind:9001,peer:127.0.0.1:9002",
            "uart0",
        )
        async with asyncio.timeout(100):
            while not dataset.done():
                await asyncio.sleep(1)
                await send(router, b"ot dataset active -x")
        provision = True
        while True:
            booted.clear()
            board = await boot(
                "board",
                "/run/lab/reflex.bin",
                "/firmware/matter_reflex.elf",
                "user,restrict=yes",
                "bind:9002,peer:127.0.0.1:9001",
                "usb-jtag",
            )
            await asyncio.wait_for(booted.wait(), 45)
            if provision:
                await send(board, b"key " + (RUN / "http-key").read_bytes())
                await send(board, b"dataset " + dataset.result())
                provision = False
            while not (RUN / "power-off").exists():
                await send(board, b"status")
                await asyncio.sleep(1)
            # ESP-EMU saves the board's NVS/fabric on a clean interrupt. The router stays up.
            board.send_signal(signal.SIGINT)
            await asyncio.wait_for(board.wait(), 20)
            (RUN / "status.json").unlink(missing_ok=True)
            (RUN / "powered-off").touch()
            while (RUN / "power-off").exists():
                await asyncio.sleep(0.2)
            (RUN / "powered-off").unlink()
    finally:
        for process in processes:
            if process.returncode is None:
                process.terminate()
        await asyncio.gather(*(p.wait() for p in processes))
        await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
