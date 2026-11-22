"""Run the healthy portion of the outage scenario on a disposable IPv6 network."""

import asyncio
import json
import os
import secrets
import subprocess
import sys
import time
from contextlib import ExitStack
from ipaddress import ip_network
from pathlib import Path
from uuid import uuid4


def configure_ha(server_address):
    # Runs inside the new HA container. The temporary admin token stays there.
    import requests
    from setup import setup

    base = "http://127.0.0.1:8123"
    session = requests.Session()
    for _ in range(120):
        try:
            if session.get(base + "/api/onboarding", timeout=2).ok:
                break
        except requests.ConnectionError:
            pass
        time.sleep(1)
    else:
        raise RuntimeError("Temporary Home Assistant did not start")

    response = session.post(
        base + "/api/onboarding/users",
        json={
            "name": "Outage Lab",
            "username": "lab",
            "password": secrets.token_hex(24),
            "client_id": base + "/",
            "language": "en",
        },
        timeout=30,
    )
    response.raise_for_status()
    response = session.post(
        base + "/auth/token",
        data={
            "grant_type": "authorization_code",
            "code": response.json()["auth_code"],
            "client_id": base + "/",
        },
        timeout=10,
    )
    response.raise_for_status()
    token = response.json()["access_token"]
    os.environ["HA_TOKEN"] = token
    session.headers.update(Authorization="Bearer " + token)
    flow_url = base + "/api/config/config_entries/flow"
    flow = session.post(flow_url, json={"handler": "matter"}, timeout=30)
    flow.raise_for_status()
    response = session.post(
        flow_url + "/" + flow.json()["flow_id"],
        json={"url": f"ws://{server_address}:5580/ws"},
        timeout=30,
    )
    response.raise_for_status()
    if response.json()["type"] != "create_entry":
        raise RuntimeError(
            f"Could not connect HA to the test Matter Server: {response.json().get('errors')}"
        )
    entities = asyncio.run(setup(base, json.load(sys.stdin)))
    # Only the disposable HA container reads this private file on its /config tmpfs.
    with os.fdopen(
        os.open("/config/lab-observer", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
        "w",
    ) as f:
        json.dump([token, entities["light"]], f)


def wait_for_entity(online):
    import requests

    token, entity = json.loads(Path("/config/lab-observer").read_text())
    for _ in range(170):
        response = requests.get(
            "http://127.0.0.1:8123/api/states/" + entity,
            headers={"Authorization": "Bearer " + token},
            timeout=5,
        )
        if response.status_code == 404:
            ready = False
        else:
            response.raise_for_status()
            ready = response.json()["state"] in {"on", "off"}
        if ready == online:
            return
        time.sleep(1)
    raise RuntimeError(
        f"Timed out waiting for HA entity {entity} to become {'available' if online else 'unavailable'}"
    )


def smoke():
    from lab import (
        FIXTURES,
        PODMAN,
        SCRIPT,
        call,
        eventually,
        phase,
        podman,
        subscribed,
        subscriptions,
    )

    project = "reflex-smoke-" + uuid4().hex[:12]
    mount = str(Path(__file__).resolve().parent) + ":/app/reflex-lab:ro,z"
    matter = os.environ["REFLEX_LAB_IMAGE"]
    ha = os.environ["REFLEX_LAB_HA_IMAGE"]
    prefix = project + "-"

    def peer_call(peer, command):
        return call(peer, command, prefix=prefix)

    with ExitStack() as cleanup:
        podman("network", "create", "--internal", "--disable-dns", "--ipv6", project)
        cleanup.callback(podman, "network", "rm", project)

        def start(role, image, options, args):
            name = prefix + role
            podman(
                "run",
                "-d",
                "--name",
                name,
                "--network",
                project,
                *options,
                image,
                *args,
                timeout=180,
            )
            cleanup.callback(podman, "rm", "--force", "--volumes", name)

        for role in FIXTURES:
            start(
                role,
                matter,
                [
                    "--env",
                    "LAB_INTERFACE=eth0",
                    "--volume",
                    mount,
                    "--tmpfs",
                    "/data:rw,mode=1777",
                    "--entrypoint",
                    "node",
                ],
                [SCRIPT, role],
            )
        # Keep the address stored by HA across the Matter Server restart.
        net = json.loads(podman("network", "inspect", project))[0]
        subnets = (ip_network(n["subnet"]) for n in net["subnets"])
        server_ip = str(next(n for n in subnets if n.version == 4)[-2])
        start(
            "server",
            matter,
            ["--ip", server_ip],
            [
                "--primary-interface",
                "eth0",
                "--disable-ota",
                "--enable-test-net-dcl",
                "--dcl-offline",
            ],
        )
        config = "default_config:\nautomation: !include automations.yaml\n"
        start(
            "ha",
            ha,
            [
                "--tmpfs",
                "/config:rw,mode=1777",
                "--volume",
                mount,
                "--entrypoint",
                "sh",
            ],
            [
                "-ec",
                (
                    'cd /config && echo "$1" > configuration.yaml && echo "[]" > automations.yaml'
                    " && exec python -m homeassistant -c /config --skip-pip"
                ),
                "sh",
                config,
            ],
        )

        def pairing_code(peer):
            if podman(
                "exec",
                prefix + peer,
                "sh",
                "-c",
                "if test -S /tmp/reflex-lab.sock; then echo ready; fi",
            ):
                return peer_call(peer, "info")["code"]
            return None

        codes = [eventually(pairing_code, peer, peer) for peer in FIXTURES]
        subprocess.run(
            [
                *PODMAN,
                "exec",
                "-i",
                prefix + "ha",
                "python",
                "/app/reflex-lab/ha_smoke.py",
                server_ip,
            ],
            input=json.dumps(codes),
            text=True,
            timeout=360,
            check=True,
        )
        controller = phase(None, 4, peer_call=peer_call)
        eventually(subscribed, "Home Assistant subscriptions", controller, peer_call)
        previous = subscriptions(controller, peer_call)
        podman("stop", "--time", "10", prefix + "server")
        peer_call("targets", "reset")
        peer_call("button", "double")
        peer_call("button", "long")
        time.sleep(4)
        if peer_call("targets", "snapshot") != {"commands": [], "on": False, "tilt": 0}:
            raise RuntimeError(
                "Actuator received a command while its only controller was stopped"
            )
        print("Both peers stayed available with Matter Server offline.", flush=True)

        def wait_ha(state):
            podman(
                "exec",
                prefix + "ha",
                "python",
                "/app/reflex-lab/ha_smoke.py",
                state,
                timeout=180,
            )

        wait_ha("offline")
        podman("start", prefix + "server")

        def resubscribed():
            # The peer may still list its old controller session after server loss.
            current = subscriptions(controller, peer_call)
            return all(new - old for new, old in zip(current, previous, strict=True))

        eventually(
            resubscribed,
            "new controller subscriptions at both peers after restart",
            timeout=180,
        )
        wait_ha("online")
        phase(controller, 4, peer_call=peer_call)


if __name__ == "__main__":
    if sys.argv[1] in {"online", "offline"}:
        wait_for_entity(sys.argv[1] == "online")
    else:
        configure_ha(sys.argv[1])
