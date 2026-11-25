"""Create the isolated Linux backbone used by the border router and Matter processes."""

import fcntl
import os
import select
import socket
import struct
import subprocess
import sys
import time

combined = "combined" in sys.argv
if combined:
    from pyroute2 import IPRoute

    with IPRoute() as netlink:
        netlink.link("add", ifname="tap0", kind="bridge", br_mcast_snooping=0)

for interface in ("reflex0", "harouter0") if combined else ("tap0",):
    with open("/dev/net/tun", "r+b", buffering=0) as tun:
        fcntl.ioctl(tun, 0x400454CA, struct.pack("16sH", interface.encode(), 0x1002))
        fcntl.ioctl(tun, 0x400454CB, 1)
    if combined:
        subprocess.run(
            ["ip", "link", "set", interface, "master", "tap0", "up"], check=True
        )
subprocess.run(["ip", "link", "set", "tap0", "up"], check=True)
if combined or "wifi" in sys.argv:
    from scapy.all import (
        Ether,
        ICMPv6ND_RA,
        ICMPv6NDOptPrefixInfo,
        IPv6,
        get_if_hwaddr,
        sendp,
    )

    # Supply the private IPv6 LAN prefix normally advertised by a home router.
    # HA and its peers share this namespace and remain reachable with HA stopped.
    subprocess.run(
        ["ip", "-6", "addr", "add", "fd42:5246:1::1/64", "dev", "tap0"], check=True
    )
    subprocess.run(["ip", "-6", "addr", "add", "fe80::1/64", "dev", "tap0"], check=True)
    packet = (
        Ether(src=get_if_hwaddr("tap0"), dst="33:33:00:00:00:01")
        / IPv6(src="fe80::1", dst="ff02::1", hlim=255)
        / ICMPv6ND_RA(routerlifetime=0)
        / ICMPv6NDOptPrefixInfo(prefix="fd42:5246:1::", prefixlen=64, L=1, A=1)
    )
    # Each ESP-EMU TAP backend addresses outbound unicast to its own host TAP
    # MAC, even when the IPv6 destination is the other station on the same LAN.
    # Deliver those frames to the peer as an AP would. The bridge already
    # handles multicasts and the Linux host receives its own unicast normally.
    stations = {}
    gateways = {}
    ports = []
    if combined:
        # ETH_P_ALL receives ingress before Linux passes the frame to the bridge.
        for interface in ("reflex0", "harouter0"):
            port = socket.socket(
                socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0003)
            )
            port.bind((interface, 0))
            ports.append(port)
            gateways[port] = port.getsockname()[4]

    next_advert = 0
    while True:
        now = time.monotonic()
        if now >= next_advert:
            sendp(packet, iface="tap0", verbose=False)
            next_advert = now + 2
        readable, _, _ = select.select(
            ports, [], [], max(0, next_advert - time.monotonic())
        )
        for port in readable:
            frame, origin = port.recvfrom(65535)
            if (
                origin[2] == socket.PACKET_OUTGOING
                or len(frame) < 54
                or frame[12:14] != b"\x86\xdd"
            ):
                continue
            source, destination = frame[22:38], frame[38:54]
            if source != bytes(16):
                stations[source] = (port, frame[6:12])
            peer = stations.get(destination)
            if frame[:6] == gateways[port] and peer and peer[0] != port:
                peer[0].send(peer[1] + frame[6:])

else:
    os.execlp("sleep", "sleep", "infinity")
