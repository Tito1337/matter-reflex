"""Wait for the Thread input's SRP advertisement and the LAN's IPv6 route."""

import socket
import subprocess
import sys
import time

from zeroconf import IPVersion, Zeroconf

service = "_matterc._udp.local."
name = f"{sys.argv[1]}.{service}"
discovery = Zeroconf(
    interfaces=[socket.if_nametoindex("tap0")], ip_version=IPVersion.V6Only
)
try:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        info = discovery.get_service_info(service, name, timeout=2000)
        for address in info.parsed_scoped_addresses() if info else ():
            if (
                subprocess.run(
                    ["ping", "-6", "-c", "1", "-W", "2", address],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode
                == 0
            ):
                print(address)
                sys.exit(0)
        time.sleep(1)
    raise RuntimeError(
        "Thread commissioning service did not become routable on the LAN"
    )
finally:
    discovery.close()
