"""Exercise the provisioning console with a pseudo-terminal and synthetic data."""

import json
import os
import select
import subprocess
import sys
from pathlib import Path

import pytest

CONSOLE = Path(__file__).resolve().parents[1] / "tools" / "board_console.py"
STATUS = b"thread=attached fabrics=1 node=0000000000000001 active=0 rules= executions=0\n"
DATASET = "0e080000000000010000"


def read_line(fd):
    line = bytearray()
    while len(line) < 512:
        assert select.select([fd], [], [], 5)[0], "Timed out waiting for a console command"
        line.extend(os.read(fd, 1))
        if line.endswith(b"\n"):
            return bytes(line)
    pytest.fail("Console command exceeds its maximum length")


@pytest.mark.parametrize("value", [DATASET, json.dumps({"success": True, "result": {"tlv": DATASET}})])
def test_dataset_transfer(value):
    master, slave = os.openpty()
    try:
        with subprocess.Popen(
            [sys.executable, CONSOLE, "dataset"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "ESPPORT": os.ttyname(slave)},
        ) as child:
            try:
                child.stdin.write(value.encode())
                child.stdin.close()
                assert read_line(master) == b"status\n"
                os.write(master, b"REFLEX ERROR\n" + STATUS)
                assert read_line(master) == b"dataset " + DATASET.encode() + b"\n"
                os.write(master, b"REFLEX OK\n")
                assert child.wait(timeout=5) == 0
                assert child.stdout.read().strip() == b"REFLEX OK"
                assert child.stderr.read() == b""
            finally:
                if child.poll() is None:
                    child.kill()
    finally:
        os.close(master)
        os.close(slave)


@pytest.mark.parametrize("value", ["0", "xyz", '{"success":false}', "01" * 255])
def test_invalid_dataset_rejected_before_opening_device(value):
    result = subprocess.run(
        [sys.executable, CONSOLE, "dataset"],
        input=value,
        capture_output=True,
        text=True,
        env={**os.environ, "ESPPORT": "/does-not-exist"},
        timeout=5,
        check=False,
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert result.stderr.strip() == "Invalid dataset response"
