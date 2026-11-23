"""Exercise the shipping C6 image with only the lamp's secure replies interrupted."""

import re
import subprocess
import time

from lab import PODMAN, eventually, podman

from . import boot_milestones


def verify_toggle_recovery(board, reflex, peer_call, prefix):
    # Enter only the disposable TAP container's network namespace. CASE messages
    # use session ID zero. Drop encrypted replies from the lamp to the board,
    # including its priming report, while it can still receive both invocations.
    pid = podman("inspect", "--format", "{{.State.Pid}}", prefix + "tap")
    nft = [*PODMAN, "unshare", "nsenter", f"--net=/proc/{int(pid)}/ns/net", "nft"]
    rules = """table ip6 reflex_toggle_test {
        chain output {
            type filter hook output priority 0; policy accept;
            oifname "tap0" udp sport 5571 @th,72,16 != 0 counter drop
        }
    }"""
    board.power_off()
    log = board.run / "board.log"
    offset = log.stat().st_size
    peer_call("targets", "reset")
    peer_call("button", "double")  # Historical on reboot, must never reach the lamp.

    def trace():
        with log.open("rb") as stream:
            stream.seek(offset)
            return stream.read()

    expected = []

    def lamp_matches():
        state = peer_call("targets", "snapshot")
        commands = state["commands"]
        if commands != expected[: len(commands)]:
            raise AssertionError(f"Unexpected authenticated lamp command: {state}")
        return commands == expected and state["on"] == (len(expected) % 2 == 1)

    subprocess.run([*nft, "-f", "-"], input=rules, text=True, check=True)
    try:
        board.power_on()

        def button_ready():
            data = trace()
            # The target request proves CASE is active. Only the button can
            # return a SubscribeResponse across the filter.
            return re.search(
                rb"Msg TX[^\n]*:5571\][^\n]*IM:SubscribeRequest", data
            ) and re.search(rb"Msg RX[^\n]*IM:SubscribeResponse", data)

        eventually(
            button_ready, "fresh button subscription and active lamp CASE", timeout=70
        )
        assert lamp_matches(), "offline gesture replayed"
        for _ in range(2):
            peer_call("button", "double")
            expected.append({"sender": reflex, "command": "toggle"})
            eventually(
                lamp_matches, "one toggle and the expected lamp state", timeout=8
            )
        filtered = subprocess.check_output(
            [*nft, "list", "table", "ip6", "reflex_toggle_test"], text=True
        )
        assert re.search(r"counter packets [1-9]\d*", filtered), (
            "no lamp replies were delayed"
        )
        assert "both_subscribe_responses" not in boot_milestones(trace().splitlines())
    finally:
        subprocess.run(
            [*nft, "delete", "table", "ip6", "reflex_toggle_test"], check=True
        )

    eventually(
        lambda: "both_subscribe_responses" in boot_milestones(trace().splitlines()),
        "lamp subscription completion",
        timeout=90,
    )
    time.sleep(1)
    assert lamp_matches(), "unexpected command after lamp recovery"
    print(
        "Verified two authenticated native Toggles, off → on → off, before the lamp subscription completed.",
        flush=True,
    )
