"""Send a bounded command to the XIAO console. Secrets arrive only on stdin."""
import argparse
import json
import os
import re
import select
import sys
import termios
import time
import tty


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "pair", "dataset", "key"))
    args = parser.parse_args()
    message = args.command
    if message == "dataset":
        reply = json.load(sys.stdin)
        value = reply.get("result", {}).get("tlv", "")
        if reply.get("success") is not True or not re.fullmatch(r"(?:[\da-fA-F]{2}){1,254}", value):
            raise SystemExit("Invalid dataset response")
        message += " " + value
    elif message == "key":
        value = sys.stdin.readline().strip()
        if not re.fullmatch(r"[\da-fA-F]{64}", value):
            raise SystemExit("Invalid provisioning key")
        message += " " + value
    # Linux CDC-ACM maps modem control transitions to the ESP32-C6 reset
    # circuit. Open the raw terminal without changing RTS/DTR for status reads.
    fd = os.open(os.environ["ESPPORT"], os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        tty.setraw(fd)
        attrs = termios.tcgetattr(fd)
        attrs[2] &= ~termios.HUPCL
        attrs[4] = attrs[5] = termios.B115200
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        termios.tcflush(fd, termios.TCIFLUSH)
        sent = False
        next_probe = 0.0
        deadline = time.monotonic() + 12
        pending = b""
        while time.monotonic() < deadline:
            if not sent and time.monotonic() >= next_probe:
                os.write(fd, b"status\n")
                next_probe = time.monotonic() + 1
            if b"\n" not in pending:
                if not select.select([fd], [], [], 0.3)[0]:
                    continue
                data = os.read(fd, 1024)
                if not data:
                    raise SystemExit("USB disconnected")
                pending = (pending + data)[-4096:]
                continue
            raw, pending = pending.split(b"\n", 1)
            line = raw.decode("ascii", errors="ignore").strip()
            if re.fullmatch(
                r"thread=(attached|detached) fabrics=\d+ node=[a-f0-9]{16} active=[01] rules=[a-f0-9]{0,64} executions=\d+", line
            ):
                if args.command == "status":
                    print(line)
                    return
                if not sent:
                    os.write(fd, (message + "\n").encode("ascii"))
                    sent = True
                continue
            if line == "REFLEX OK":
                print("REFLEX OK")
                return
            if line == "REFLEX ERROR":
                raise SystemExit("Command rejected by the board")
    finally:
        os.close(fd)
    raise SystemExit("No response from the Reflex console")


if __name__ == "__main__":
    main()
