"""Create the isolated Linux backbone used by the border router and Matter processes."""

import fcntl
import os
import struct
import subprocess

with open("/dev/net/tun", "r+b", buffering=0) as tun:
    fcntl.ioctl(tun, 0x400454CA, struct.pack("16sH", b"tap0", 0x1002))
    fcntl.ioctl(tun, 0x400454CB, 1)

subprocess.run(["ip", "link", "set", "tap0", "up"], check=True)
os.execlp("sleep", "sleep", "infinity")
