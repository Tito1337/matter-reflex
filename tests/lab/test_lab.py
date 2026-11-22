"""Regression tests for target evidence and recovery of the dedicated controller."""

from types import SimpleNamespace

import lab
import pytest


@pytest.mark.parametrize(
    "failure", ["extra", "wrong_sender", "wrong_tilt", "late_duplicate"]
)
def test_reject_incorrect_target_receipts(monkeypatch, failure):
    commands = []

    def fixture(peer, action):
        if action == "double":
            commands.append(
                {"command": "on", "sender": "43" if failure == "wrong_sender" else "42"}
            )
            if failure == "extra":
                commands.append(commands[-1])
        elif action == "long":
            commands.append(
                {
                    "command": "tilt",
                    "value": 7500 if failure == "wrong_tilt" else 2500,
                    "sender": "42",
                }
            )
        elif action == "snapshot":
            return {"commands": list(commands), "on": True, "tilt": 2500}

    def settle(_):
        if failure == "late_duplicate":
            commands.append(commands[-1])

    monkeypatch.setattr(lab.time, "sleep", settle)
    with pytest.raises(RuntimeError):
        lab.phase("42", 1, peer_call=fixture)


@pytest.mark.parametrize("failure", [None, "before", "outage", "interrupt"])
def test_outage_restores_server(monkeypatch, failure):
    running = True
    operations = []

    def podman(command, *args):
        nonlocal running
        if command == "inspect":
            return str(running).lower()
        operations.append(command)
        running = command == "start"

    class HAState:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self):
            return self.content

    def urlopen(req, timeout):
        response = HAState()
        state = (
            "unavailable"
            if not running
            else ("on" if req.full_url.endswith("synced") else "off")
        )
        response.content = lab.json.dumps({"state": state}).encode()
        return response

    def phase(sender, settle, **kwargs):
        origin = sender or "10"
        operations.append(origin)
        if failure == "before" and len(operations) == 1:
            raise RuntimeError("preflight")
        if not running and failure == "outage":
            raise RuntimeError("direct command failed")
        if not running and failure == "interrupt":
            raise KeyboardInterrupt
        return origin

    monkeypatch.setenv("HA_TOKEN", "local-test")
    monkeypatch.setattr(lab, "podman", podman)
    monkeypatch.setattr(lab, "urlopen", urlopen)
    monkeypatch.setattr(
        lab,
        "call",
        lambda *args: {
            "fabrics": 1,
            "subscribers": [{"node": "10", "id": "1"}, {"node": "42", "id": "2"}],
        },
    )
    monkeypatch.setattr(lab, "phase", phase)
    monkeypatch.setattr(lab.time, "sleep", lambda seconds: None)
    args = SimpleNamespace(
        reflex_node="42",
        controller_node=None,
        ha_url="http://lab",
        synced_entity="synced",
        active_entity="active",
        server="test-server",
        settle=4,
    )
    if failure is None:
        lab.run(args)
        assert operations == ["10", "stop", "42", "start", "10"]
    else:
        with pytest.raises(
            KeyboardInterrupt if failure == "interrupt" else RuntimeError
        ):
            lab.run(args)
        assert operations == (
            ["10"] if failure == "before" else ["10", "stop", "42", "start"]
        )
    assert running


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        ("sender", "After contact"),
        ("fade", "After occupancy"),
        ("state", "contact command and resulting actuator state"),
        ("same-side", "without a new crossing"),
        ("baseline", "during sensor rearm"),
    ],
)
def test_sensor_phase_rejects_incorrect_evidence(monkeypatch, fault, message):
    from ha_smoke import sensor_phase
    from sensor_cases import SENSOR_CASES

    cases = {case.sensor: case for case in SENSOR_CASES}
    commands = []
    state = {"plug": False, "on": False, "level": 1, "lift": 0}

    def fixture(peer, command):
        if command == "snapshot":
            return {"commands": []}
        if command == "sensor-snapshot":
            return {"commands": commands.copy(), **state}
        name = command.removeprefix("sensor ")
        if name in cases:
            case = cases[name]
            receipt = {**case.receipt, "sender": "42"}
            if fault == "sender":
                receipt["sender"] = "99"
            if fault == "fade" and name == "occupancy":
                receipt["transition"] = 0
            commands.append(receipt)
            if fault != "state":
                state.update(case.state_change)
        elif name == fault:
            commands.append(commands[-1])

    def check_now(check, description, *args, timeout):
        if not check(*args):
            raise RuntimeError(description)

    monkeypatch.setattr(lab, "eventually", check_now)
    with pytest.raises(RuntimeError, match=message):
        sensor_phase("42", fixture, settle=0)
