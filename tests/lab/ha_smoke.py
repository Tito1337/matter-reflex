"""Commission an isolated HA fabric and exercise controller loss and recovery."""

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

from sensor_cases import SENSOR_CASES


def configure_esphome(session, base, flow_url, host, key):
    # Resolve the real ESPHome service on the isolated LAN. Using its ULA
    # keeps the HA connection on tap0 when Podman also supplies eth0.
    import socket
    from ipaddress import IPv6Address

    from zeroconf import IPVersion, Zeroconf

    service = "_esphomelib._tcp.local."
    with Zeroconf(
        interfaces=[socket.if_nametoindex("tap0")], ip_version=IPVersion.V6Only
    ) as discovery:
        for _ in range(30):
            info = discovery.get_service_info(
                service, f"{host.split('.')[0]}.{service}", timeout=2000
            )
            if info and info.server == host + ".":
                addresses = [
                    ip
                    for ip in info.parsed_addresses()
                    if ":" in ip and IPv6Address(ip) in ip_network("fd42:5246:1::/64")
                ]
                if addresses:
                    break
            time.sleep(1)
        else:
            raise RuntimeError(
                f"ESPHome DNS-SD did not advertise {host} and its IPv6 LAN address"
            )

    form = session.post(flow_url, json={"handler": "esphome"}, timeout=30)
    form.raise_for_status()
    url = flow_url + "/" + form.json()["flow_id"]
    for fields in (
        {"host": addresses[0], "port": info.port},
        {"noise_psk": key},
    ):
        result = session.post(url, json=fields, timeout=45)
        result.raise_for_status()
        step = result.json()
        if step.get("errors"):
            raise RuntimeError(
                f"ESPHome setup failed at {step.get('step_id')}: {step['errors']}"
            )
    if step["type"] != "create_entry":
        raise RuntimeError(f"ESPHome setup ended at {step.get('step_id')}")
    api_id = step["result"]["entry_id"]
    action = host.split(".")[0].replace("-", "_") + "_reflex_exchange"
    for _ in range(60):
        services = session.get(base + "/api/services", timeout=10)
        services.raise_for_status()
        if any(
            d["domain"] == "esphome" and action in d["services"]
            for d in services.json()
        ):
            break
        time.sleep(1)
    else:
        raise RuntimeError("Encrypted ESPHome rule exchange action was unavailable")
    return api_id


def configure_ha(server_address):
    # Runs inside the new HA container. The temporary admin token stays there.
    import requests
    from setup import commission, setup

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
    commissioning = json.load(sys.stdin)
    asyncio.run(commission(base, commissioning["codes"]))
    entry_id = None
    api_id = None
    if commissioning.get("api_key"):
        api_id = configure_esphome(
            session, base, flow_url, commissioning["api_host"], commissioning["api_key"]
        )
    if commissioning["with_board"]:
        flow = session.post(flow_url, json={"handler": "matter_reflex"}, timeout=30)
        flow.raise_for_status()
        form = flow.json()
        if form["type"] != "form":
            raise RuntimeError(
                f"Could not discover the commissioned Reflex board: {form['type']}"
            )
        (choice,) = next(
            f["options"] for f in form["data_schema"] if f["name"] == "node"
        )
        response = session.post(
            flow_url + "/" + form["flow_id"], json={"node": choice[0]}, timeout=45
        )
        response.raise_for_status()
        if api_id:
            if response.json().get("step_id") != "esphome":
                raise RuntimeError("Reflex did not offer the encrypted ESPHome rules channel")
            response = session.post(
                flow_url + "/" + form["flow_id"],
                json={"esphome_entry_id": api_id},
                timeout=45,
            )
            response.raise_for_status()
        result = response.json()
        if result["type"] != "create_entry":
            raise RuntimeError(
                f"Reflex setup failed at {result.get('step_id')}: {result.get('errors')}"
            )

        entry_id = result["result"]["entry_id"]
        print("Configured the Matter Reflex integration.", flush=True)
    entities = asyncio.run(
        setup(
            base,
            [],
            sensors=True,
            mode=commissioning.get("mode", "fallback"),
            reflex_entry_id=entry_id,
            toggle=commissioning.get("toggle", False),
            thread_contact=commissioning.get("thread_contact", False),
        )
    )
    # Only the disposable HA container reads this private file on its /config tmpfs.
    with os.fdopen(
        os.open("/config/lab-observer", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
        "w",
    ) as f:
        json.dump([token, entities["light"], entry_id], f)


def wait_for_entity(online):
    import requests

    token, entity, _ = json.loads(Path("/config/lab-observer").read_text())
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


def sensor_phase(sender, peer_call, settle=4):
    from lab import eventually

    expected = []
    state = {"plug": False, "on": False, "level": 1, "lift": 0}

    def snapshot():
        return peer_call("targets", "sensor-snapshot")

    def delivered(previous, name):
        observed = snapshot()
        if observed["commands"] == previous:
            return False
        if observed["commands"] != expected:
            raise RuntimeError(
                f"After {name}: expected {expected}, received {observed}"
            )
        return observed == {"commands": expected, **state}

    button_targets = peer_call("targets", "snapshot")
    if snapshot()["commands"]:
        raise RuntimeError(
            f"Unexpected sensor command before the first input: {snapshot()}"
        )
    for case in SENSOR_CASES:
        previous = expected.copy()
        origin = sender[case.sensor] if isinstance(sender, dict) else sender
        peer_call("button", "sensor " + case.sensor)
        if origin is None:
            time.sleep(settle)
            observed = snapshot()
            if observed != {"commands": expected, **state}:
                raise RuntimeError(
                    f"{case.sensor} should have stayed quiet: {observed}"
                )
            continue
        expected.append({**case.receipt, "sender": origin})
        state.update(case.state_change)
        eventually(
            delivered,
            f"{case.sensor} command and resulting actuator state",
            previous,
            case.sensor,
            timeout=20,
        )
    peer_call("button", "sensor same-side")
    time.sleep(settle)
    if snapshot() != {"commands": expected, **state}:
        raise RuntimeError(
            f"Sensor rule fired again without a new crossing: {snapshot()}"
        )
    # Returning to closed, clear, dry or exactly the threshold rearms each rule.
    peer_call("button", "sensor baseline")
    time.sleep(settle)
    if snapshot() != {"commands": expected, **state}:
        raise RuntimeError(
            f"Duplicate or unexpected actuation during sensor rearm: {snapshot()}"
        )
    if peer_call("targets", "snapshot") != button_targets:
        raise RuntimeError(
            "A sensor input changed the gesture-controlled lamp or blind"
        )
    peer_call("targets", "sensor-reset")
    print(
        f"Verified sensor edges, actuator states and expected senders {sender}.",
        flush=True,
    )


def measure_cold_boot(board, reflex, peer_call, restored, fallback):
    from c6 import boot_milestones
    from lab import eventually, subscriptions

    previous = subscriptions(reflex, peer_call)
    board.power_off()
    trace = board.run / "board.log"
    offset = trace.stat().st_size
    peer_call("targets", "reset")
    start = time.monotonic()
    board.power_on()
    eventually(
        restored, "fresh ESP subscriptions on both peers", reflex, previous, timeout=180
    )
    subscribed_at = time.monotonic() - start
    if fallback:
        board.wait("fallback boot grace to expire", lambda state: state["active"])
    if peer_call("targets", "snapshot")["commands"]:
        raise RuntimeError("Unexpected replay before the first fresh press")
    pressed_at = time.monotonic() - start
    peer_call("button", "double")
    expected = [{"command": "on", "sender": reflex}]

    def received():
        state = peer_call("targets", "snapshot")
        if state["commands"] and state["commands"] != expected:
            raise RuntimeError(f"Unexpected cold-boot command: {state}")
        return state["commands"] == expected and state["on"]

    eventually(received, "authenticated lamp On after reboot", timeout=20)
    observed_at = time.monotonic() - start
    board.wait(
        "one successful execution on restarted ESP", lambda s: s["executions"] == 1
    )
    with trace.open("rb") as log:
        log.seek(offset)
        print("ESP boot (ms):", boot_milestones(log), flush=True)
    print(
        f"Cold boot: both subscriptions {subscribed_at:.3f}s, "
        f"press {pressed_at:.3f}s, verified lamp On {observed_at:.3f}s.",
        flush=True,
    )


def smoke(with_board=False, mode="fallback", cold_boots=0, toggle=False, *, transport="thread", sleepy_input=False):
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

        def start(role, image, options, args, network=project):
            name = prefix + role
            if with_board and role in (*FIXTURES, "ha", "server"):
                network = "container:" + prefix + "tap"
            podman(
                "run",
                "-d",
                "--name",
                name,
                "--network",
                network,
                *options,
                image,
                *args,
                timeout=180,
            )
            cleanup.callback(podman, "rm", "--force", "--time", "0", "--volumes", name)

        board = None
        integration = []
        if with_board:
            from c6 import ROOT, EmulatedBoard

            if transport == "thread":
                board = EmulatedBoard(cleanup, start, prefix, ha)
            else:
                from c6.esphome import NetworkBoard

                board = NetworkBoard(cleanup, start, prefix, ha, transport)
            integration = [
                "--volume",
                str(ROOT / "custom_components") + ":/config/custom_components:ro,z",
            ]

        for role in FIXTURES:
            start(
                role,
                matter,
                [
                    "--env",
                    "LAB_INTERFACE=" + ("tap0" if board else "eth0"),
                    "--volume",
                    mount,
                    "--tmpfs",
                    "/data:rw,mode=1777",
                    "--entrypoint",
                    "node",
                ],
                [SCRIPT, role, "sensors"],
            )
        # Keep the address stored by HA across the Matter Server restart.
        net = json.loads(podman("network", "inspect", project))[0]
        subnets = (ip_network(n["subnet"]) for n in net["subnets"])
        server_ip = str(next(n for n in subnets if n.version == 4)[-2])
        server_options = [] if board else ["--ip", server_ip]
        if board and transport == "thread":
            # The standalone firmware uses the vendor cluster for management.
            # ESPHome profiles exercise the encrypted API with the stock controller.
            server_options = [
                "--volume", mount,
                "--env", "NODE_OPTIONS=--import=/app/reflex-lab/reflex-cluster.mjs",
            ]
        start(
            "server",
            matter,
            server_options,
            [
                "--primary-interface",
                "tap0" if board else "eth0",
                "--disable-ota",
                "--enable-test-net-dcl",
                "--dcl-offline",
            ],
        )
        config = "homeassistant:\n  unit_system: metric\ndefault_config:\nautomation: !include automations.yaml\n"
        start(
            "ha",
            ha,
            [
                "--tmpfs",
                "/config:rw,mode=1777",
                *integration,
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
        if board:
            # Prove the original radio is routing the input before starting the
            # second router. Keep the Matter node commissioning order stable.
            contact_code = board.contact_code() if transport == "combined" else None
            codes.append(board.pairing_code())
            if contact_code:
                codes.append(contact_code)
        subprocess.run(
            [
                *PODMAN,
                "exec",
                "-i",
                prefix + "ha",
                "python",
                "/app/reflex-lab/ha_smoke.py",
                "127.0.0.1" if board else server_ip,
            ],
            input=json.dumps(
                {
                    "codes": codes,
                    "with_board": bool(board),
                    "mode": mode,
                    "toggle": toggle,
                    "api_key": getattr(board, "api_key", None),
                    "api_host": getattr(board, "api_host", None),
                    "thread_contact": transport == "combined",
                }
            ),
            text=True,
            timeout=360,
            check=True,
        )

        def wait_ha(state, *args):
            return podman(
                "exec",
                prefix + "ha",
                "python",
                "/app/reflex-lab/ha_smoke.py",
                state,
                *args,
                timeout=180,
            )

        def restored(node, previous):
            current = subscriptions(node, peer_call)
            return all(new - old for new, old in zip(current, previous, strict=True))

        if board:
            from modes import ADVANCED, executor, handlers

            ready = board.wait(
                "installed rules with a live HA lease",
                lambda s: s["fabrics"] == 1 and s["rules"] and not s["active"],
            )
            reflex = ready["node"]
            eventually(subscribed, "ESP subscriptions", reflex, peer_call, timeout=180)

            def controller_node():
                nodes = [
                    {v["node"] for v in peer_call(peer, "info")["subscribers"]}
                    for peer in FIXTURES
                ]
                candidates = set.intersection(*nodes) - {reflex}
                if len(candidates) == 1:
                    return candidates.pop()
                return None

            controller = eventually(
                controller_node, "distinct subscribed HA controller"
            )
            nodes = {"esp": reflex, "ha": controller, "none": None}
            direct_rows = sum(handlers(mode, rule)[0] == "esp" for rule in ADVANCED)

            if transport == "combined":
                direct_rows += sum(
                    handlers(mode, rule)[0] == "esp"
                    for rule in ("contact", "long_press")
                )

            def cross_network_press(offline=None):
                if offline == "ha":
                    board.wait_thread_traffic()
                peer_call("targets", "reset")
                expected = {"commands": [], "on": False, "tilt": 0}
                for input_key, rule, command in (
                    ("o", "contact", "on"),
                    ("p", "long_press", "off"),
                ):
                    board.set_contact(input_key)
                    sender = nodes[executor(mode, rule, offline)]
                    if sender is not None:
                        expected["commands"].append(
                            {"sender": sender, "command": command}
                        )
                        expected["on"] = command == "on"
                        eventually(
                            lambda: peer_call("targets", "snapshot") == expected,
                            f"{rule} delivered exactly one {command} command from {sender}",
                            timeout=60,
                        )
                board.set_contact("c")
                time.sleep(4)
                if peer_call("targets", "snapshot") != expected:
                    raise RuntimeError("Duplicate or unexpected cross-network command")
                print(
                    f"Verified Thread contact and long press against LAN lamp state and senders {expected}.",
                    flush=True,
                )

            def wait_ready():
                image = wait_ha("ready", str(direct_rows)) if direct_rows else None
                board.wait(
                    "commissioned ESP and live HA lease",
                    lambda s: (
                        s["node"] == reflex
                        and s["fabrics"] == 1
                        and s["rules"]
                        and (not image or s["rules"] == image)
                        and not s["active"]
                    ),
                )

            def verify(offline=None):
                senders = {
                    name: nodes[executor(mode, name, offline)] for name in ADVANCED
                }
                if offline != "esp":
                    before = board.wait(
                        "ESP ownership phase",
                        lambda s: s["active"] == (offline == "ha"),
                    )
                print(
                    f"Testing {mode} with {offline or 'neither controller'} offline.",
                    flush=True,
                )
                phase(senders["multi_press_2"], 4, peer_call=peer_call)
                sensor_phase(senders, peer_call)
                if offline != "esp":
                    count = sum(node == reflex for node in senders.values())
                    board.wait(
                        f"exactly {count} additional ESP executions",
                        lambda s: (
                            s["executions"] == before["executions"] + count
                            and s["active"] == (offline == "ha")
                        ),
                    )
                    print(
                        f"Firmware execution counter {before['executions']} → {before['executions'] + count}.",
                        flush=True,
                    )

            wait_ready()
            if toggle:
                from c6.toggle import verify_toggle_recovery

                podman("kill", prefix + "server")
                for _ in range(cold_boots or 1):
                    verify_toggle_recovery(board, reflex, peer_call, prefix)
                return
            verify()
            if transport == "combined":
                if sleepy_input:
                    # Enter sleepy operation after commissioning, before the first input.
                    board.set_contact("s")
                board.wait_parent("host", sleepy=sleepy_input)
                cross_network_press()
        else:
            controller = phase(None, 4, peer_call=peer_call)
            sensor_phase(controller, peer_call)
        eventually(subscribed, "HA subscriptions", controller, peer_call)
        previous = subscriptions(controller, peer_call)
        if board:
            podman("kill", prefix + "server")
            verify("ha")
            for _ in range(cold_boots):
                measure_cold_boot(
                    board, reflex, peer_call, restored, mode == "fallback"
                )
            if transport in {"wifi", "combined"}:
                # Freeze Core as well as stopping Matter Server. The TAP/AP, Thread
                # input and LAN actuator are independent of both HA processes.
                podman("kill", "--signal", "STOP", prefix + "ha")
                try:
                    if transport == "combined":
                        board.stop_host_router(sleepy=sleepy_input)
                        cross_network_press("ha")
                    board.power_off()
                    board.power_on()
                    eventually(
                        subscribed,
                        "subscriptions after offline boot",
                        reflex,
                        peer_call,
                        timeout=240,
                    )
                    board.wait(
                        "restored peers and expired lease after offline boot",
                        lambda s: (
                            s["node"] == reflex
                            and s["active"]
                            and s["required"] > 0
                            and s["ready"] == s["required"]
                        ),
                        timeout=240,
                    )
                    if transport == "combined":
                        board.wait_parent("reflex", sleepy=sleepy_input)
                        cross_network_press("ha")
                    verify("ha")
                finally:
                    if transport == "combined":
                        board.restart_host_router()
                    podman("kill", "--signal", "CONT", prefix + "ha")
            if (
                podman("inspect", "--format", "{{.State.Running}}", prefix + "server")
                != "false"
            ):
                raise RuntimeError("Matter Server restarted during the outage")
        else:
            podman("stop", "--time", "10", prefix + "server")
            peer_call("targets", "reset")
            peer_call("button", "double")
            peer_call("button", "long")
            quiet_targets = peer_call("targets", "sensor-snapshot")
            for case in SENSOR_CASES:
                peer_call("button", "sensor " + case.sensor)
            time.sleep(4)
            if peer_call("targets", "sensor-snapshot") != quiet_targets:
                raise RuntimeError(
                    "Sensor command delivered with the only controller stopped"
                )
            if peer_call("targets", "snapshot") != {
                "commands": [],
                "on": False,
                "tilt": 0,
            }:
                raise RuntimeError(
                    "Actuator received a command while its only controller was stopped"
                )
            peer_call("button", "sensor baseline")

        wait_ha("offline")
        podman("start", prefix + "server")
        eventually(
            restored,
            "new HA subscriptions at both peers",
            controller,
            previous,
            timeout=180,
        )
        wait_ha("online")
        if not board:
            phase(controller, 4, peer_call=peer_call)
            sensor_phase(controller, peer_call)
            return
        wait_ready()
        verify()
        if transport == "combined":
            cross_network_press()
        previous = subscriptions(reflex, peer_call)
        board.power_off()
        # Last in-flight renewal plus the integration's ten-second reciprocal lease.
        time.sleep(15)
        verify("esp")

        def target_history():
            return [
                peer_call("targets", view) for view in ("snapshot", "sensor-snapshot")
            ]

        before_reboot = target_history()
        board.power_on()
        eventually(
            restored,
            "fresh ESP subscriptions after cold boot",
            reflex,
            previous,
            timeout=240,
        )
        # After the firmware's ten-second boot grace, standby requires a real renewal.
        time.sleep(11)
        wait_ready()
        if target_history() != before_reboot:
            raise RuntimeError(
                "A peer received a replayed command during ESP cold boot"
            )
        verify()


def wait_direct_ready(count):
    _, _, entry = json.loads(Path("/config/lab-observer").read_text())
    path = Path("/config/.storage", f"matter_reflex.{entry}.readiness")
    for _ in range(150):
        if path.exists():
            proof = json.loads(path.read_text())["data"]
            if proof["ready"].bit_count() == count:
                print(proof["hash"])
                return
        time.sleep(1)
    raise RuntimeError(f"HA never confirmed readiness for {count} direct source paths")


if __name__ == "__main__":
    if sys.argv[1] in {"online", "offline"}:
        wait_for_entity(sys.argv[1] == "online")
    elif sys.argv[1] == "ready":
        wait_direct_ready(int(sys.argv[2]))
    else:
        configure_ha(sys.argv[1])
