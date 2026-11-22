"""Execution policies shared by the HA condition and the rule compiler."""

from dataclasses import dataclass

from .const import DOMAIN


@dataclass(frozen=True)
class Handlers:
    normal: str
    degraded: str

    def __post_init__(self):
        if (
            self.normal not in HANDLERS
            or self.degraded not in HANDLERS
            or self.normal == self.degraded
        ):
            raise ValueError("Choose two different handlers, including None if needed")


HANDLERS = ("ha", "esp", "none")
DIRECT = Handlers("esp", "ha")
FALLBACK = Handlers("ha", "esp")
MODES = {"direct": DIRECT, "fallback": FALLBACK}
MODE_CONDITION = f"{DOMAIN}.mode"
ADVANCED_CONDITION = f"{DOMAIN}.handlers"


def handlers_for(kind, options):
    if kind == MODE_CONDITION:
        return MODES[options.get("mode", "fallback")]
    return Handlers(options["normal_handler"], options["degraded_handler"])


def configured_policy(config):
    """Only a mandatory top-level gate can delegate an automation to the ESP."""
    conditions = config.get("conditions", config.get("condition", []))
    if not isinstance(conditions, list):
        conditions = [conditions]
    gates = [
        item
        for item in conditions
        if isinstance(item, dict)
        and item.get("condition") in (MODE_CONDITION, ADVANCED_CONDITION)
    ]

    def has_nested_gate(item):
        if not isinstance(item, dict):
            return False
        children = item.get("conditions", [])
        if not isinstance(children, list):
            children = [children]
        return any(
            isinstance(child, dict)
            and (
                child.get("condition") in (MODE_CONDITION, ADVANCED_CONDITION)
                or has_nested_gate(child)
            )
            for child in children
        )

    if any(has_nested_gate(item) for item in conditions):
        raise ValueError("Move the Matter Reflex condition to the top level")
    if not gates:
        return None
    if len(gates) != 1 or gates[0].get("enabled", True) is not True:
        raise ValueError("Use one enabled top-level Matter Reflex condition")
    gate = gates[0]
    options = gate["options"]
    return (
        options["esp"],
        handlers_for(gate["condition"], options),
        [item for item in conditions if item is not gate],
    )
