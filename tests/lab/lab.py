"""Run the Matter outage scenario against a dedicated local Podman lab."""

import argparse
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from urllib.request import Request, urlopen

PODMAN = ["podman", "--cgroup-manager=cgroupfs"]
FIXTURES = ("button", "targets")
PREFIX = "matter-reflex-lab-"
SCRIPT = "/app/reflex-lab/peer.mjs"


def podman(*args, timeout=30):
    return subprocess.check_output([*PODMAN, *args], text=True, timeout=timeout).strip()


def call(peer, command, *, prefix=PREFIX):
    reply = json.loads(
        podman("exec", prefix + peer, "node", "/app/reflex-lab/call.mjs", command)
    )
    if "error" in reply:
        raise RuntimeError(reply["error"])
    return reply


def eventually(check, description, *args, timeout=90):
    deadline = time.monotonic() + timeout
    while True:
        result = check(*args)
        if result:
            return result
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Timed out waiting for {description}")
        time.sleep(0.5)


def exists(name):
    result = subprocess.run([*PODMAN, "container", "exists", name], check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError(f"Could not inspect {name}")
    return result.returncode == 0


def up(interface):
    def ready(peer, name):
        try:
            return call(peer, "info")
        except subprocess.CalledProcessError:
            if podman("inspect", "--format", "{{.State.Running}}", name) != "true":
                raise RuntimeError(f"{name} exited. Inspect podman logs {name}")
            return None

    for peer in FIXTURES:
        name = PREFIX + peer
        if exists(name):
            info = json.loads(podman("inspect", name))[0]
            if info["Config"]["Labels"].get("org.matter-reflex.fixture") != peer:
                raise RuntimeError(f"Existing container {name} is not a lab fixture")
            if "LAB_INTERFACE=" + interface not in info["Config"]["Env"]:
                raise RuntimeError(
                    f"{name} uses a different interface. Run lab down before changing it."
                )
            podman("start", name)
        else:
            podman(
                "run",
                "-d",
                "--name",
                name,
                "--label",
                "org.matter-reflex.fixture=" + peer,
                "--network",
                "host",
                "--env",
                "LAB_INTERFACE=" + interface,
                "--volume",
                name + "-data:/data:U",
                "--volume",
                f"{Path(__file__).resolve().parent}:/app/reflex-lab:ro,z",
                "--entrypoint",
                "node",
                os.environ["REFLEX_LAB_IMAGE"],
                SCRIPT,
                peer,
                timeout=180,
            )

        info = eventually(ready, name, peer, name)
        print(
            f"{name}: "
            + (
                "commissioned"
                if info["commissioned"]
                else f"manual pairing code {info['code']}"
            )
        )


def setup(base):
    codes = []
    for peer in FIXTURES:
        info = call(peer, "info")
        if not info["commissioned"]:
            codes.append(info["code"])
    subprocess.run(
        [
            *PODMAN,
            "run",
            "--rm",
            "-i",
            "--network",
            "host",
            "--env",
            "HA_TOKEN",
            "--volume",
            f"{Path(__file__).resolve().parent}:/lab:ro,z",
            "--entrypoint",
            "python",
            os.environ["REFLEX_LAB_HA_IMAGE"],
            "/lab/setup.py",
            base,
        ],
        input=json.dumps(codes),
        text=True,
        check=True,
    )


def down():
    for peer in FIXTURES:
        name = PREFIX + peer
        if exists(name):
            info = json.loads(podman("inspect", name))[0]
            if info["Config"]["Labels"].get("org.matter-reflex.fixture") != peer:
                raise RuntimeError(f"Existing container {name} is not a lab fixture")
            podman("rm", "--force", name)


def node_id(value):
    # HA displays node IDs in decimal. Accept hex only with an explicit 0x.
    number = int(value, 16 if value.lower().startswith("0x") else 10)
    if not 0 < number <= 0xFFFFFFEFFFFFFFFF:
        raise argparse.ArgumentTypeError("Expected an operational Matter node ID")
    return str(number)


def phase(sender, settle, *, excluded_sender=None, peer_call=call):
    def delivered(sender, previous, command, gesture):
        actual = peer_call("targets", "snapshot")["commands"]
        if actual == previous:
            return None
        origin = sender if sender is not None else actual[-1]["sender"]
        wanted = [*previous, {**command, "sender": origin}]
        if origin == excluded_sender or actual != wanted:
            raise RuntimeError(f"After {gesture}: expected {wanted}, received {actual}")
        return origin

    peer_call("targets", "reset")
    expected = []
    for gesture, command in (
        ("double", {"command": "on"}),
        ("long", {"command": "tilt", "value": 2500}),
    ):
        peer_call("button", gesture)
        previous = list(expected)

        sender = eventually(
            delivered,
            f"{gesture} command",
            sender,
            previous,
            command,
            gesture,
            timeout=15,
        )
        expected.append({**command, "sender": sender})
    time.sleep(settle)
    snapshot = peer_call("targets", "snapshot")
    if snapshot != {"commands": expected, "on": True, "tilt": 2500}:
        raise RuntimeError(f"Unexpected final target evidence: {snapshot}")
    print(
        f"Verified both gestures, target states and exactly two commands from node {sender}.",
        flush=True,
    )
    return sender


def subscriptions(node, peer_call):
    return [
        {s["id"] for s in peer_call(peer, "info")["subscribers"] if s["node"] == node}
        for peer in FIXTURES
    ]


def subscribed(node, peer_call):
    return all(subscriptions(node, peer_call))


def run(args):
    if args.reflex_node == args.controller_node:
        raise ValueError("Reflex and Matter Server must have different node IDs")
    token = os.environ["HA_TOKEN"]

    def state(entity):
        req = Request(
            f"{args.ha_url.rstrip('/')}/api/states/{entity}",
            headers={"Authorization": "Bearer " + token},
        )
        with urlopen(req, timeout=5) as response:
            return json.load(response)["state"]

    def standby():
        return state(args.synced_entity) == "on" and state(args.active_entity) == "off"

    if args.server.startswith("-") or args.server in {
        PREFIX + peer for peer in FIXTURES
    }:
        raise ValueError("Expected the dedicated Matter Server container name")
    if (
        podman(
            "inspect",
            "--type",
            "container",
            "--format",
            "{{.State.Running}}",
            args.server,
        )
        != "true"
    ):
        raise RuntimeError("The lab Matter Server must be running before the scenario")
    for peer in FIXTURES:
        if call(peer, "info")["fabrics"] != 1:
            raise RuntimeError(f"Expected {peer} on the single lab fabric")
    eventually(standby, "synchronized Reflex rules and standby")
    print("Checking both Home Assistant rules before the outage.", flush=True)
    eventually(
        subscribed,
        "both virtual peers to report a subscription from the XIAO",
        args.reflex_node,
        call,
        timeout=180,
    )
    controller = phase(
        args.controller_node, args.settle, excluded_sender=args.reflex_node
    )
    # Always attempt restoration, including when the stop itself is interrupted.
    try:
        podman("stop", "--time", "10", args.server)
        time.sleep(12)  # Ten-second device lease plus scheduling margin.
        if podman("inspect", "--format", "{{.State.Running}}", args.server) != "false":
            raise RuntimeError("The lab Matter Server did not stay down")
        eventually(
            lambda: not standby(), "Home Assistant to register the outage", timeout=30
        )
        print("Checking direct commands during the outage.", flush=True)
        phase(args.reflex_node, args.settle)
        if podman("inspect", "--format", "{{.State.Running}}", args.server) != "false":
            raise RuntimeError("Matter Server restarted during the outage")
    finally:
        podman("start", args.server)
    eventually(
        standby,
        "Home Assistant to reconnect, synchronize and renew the lease",
        timeout=180,
    )
    eventually(
        subscribed,
        "both virtual peers to report a subscription from HA",
        controller,
        call,
        timeout=90,
    )
    print("Checking handback to Home Assistant.", flush=True)
    phase(controller, args.settle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    modes.add_parser(
        "takeover", help="exercise controller loss and recovery with an emulated C6"
    ).add_argument(
        "--mode",
        choices=("fallback", "direct", "advanced", "all"),
        default="fallback",
        dest="policy",
    )
    modes.add_parser(
        "smoke-ha",
        help="create a disposable HA, controller and peers on a private IPv6 network",
    )
    modes.add_parser(
        "up", help="start persistent peers and show pairing information"
    ).add_argument("--interface", required=True)
    modes.add_parser(
        "setup", help="commission peers and install the two Reflex automations"
    ).add_argument("--ha-url", required=True)
    modes.add_parser(
        "down", help="remove the two lab containers and preserve fabric volumes"
    )
    outage = modes.add_parser(
        "run", help="exercise the configured lab fabric and restore Matter Server"
    )
    outage.add_argument(
        "--server", required=True, help="local Podman test Matter Server container"
    )
    outage.add_argument("--reflex-node", required=True, type=node_id)
    outage.add_argument(
        "--controller-node",
        type=node_id,
        help="expected HA sender (default: observe the healthy phase)",
    )
    outage.add_argument("--ha-url", required=True)
    outage.add_argument("--active-entity", required=True)
    outage.add_argument("--synced-entity", required=True)
    outage.add_argument(
        "--settle",
        type=int,
        default=4,
        choices=range(1, 61),
        metavar="SECONDS",
        help="observe targets for late duplicate commands after each phase (default 4)",
    )
    args = parser.parse_args()
    if args.mode in {"smoke-ha", "takeover", "run"}:

        def terminate(signum, frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, terminate)
    if args.mode in {"smoke-ha", "takeover"}:
        from ha_smoke import smoke

        if args.mode == "smoke-ha":
            smoke()
        else:
            policies = (
                ("direct", "fallback", "advanced")
                if args.policy == "all"
                else (args.policy,)
            )
            for policy in policies:
                smoke(with_board=True, mode=policy)
    elif args.mode == "up":
        up(args.interface)
    elif args.mode == "setup":
        setup(args.ha_url)
    elif args.mode == "down":
        down()
    else:
        run(args)


if __name__ == "__main__":
    main()
