"""Version 1 rules. Integers are big-endian and node IDs never pass through a float."""

from dataclasses import dataclass
from hashlib import sha256
from struct import Struct

MAX_RULES = 32
ROW = Struct(">IQHBBQHBhBBx")


@dataclass(frozen=True, slots=True)
class Rule:
    rule_id: int
    source: int
    source_ep: int
    event: int
    presses: int
    target: int
    target_ep: int
    op: int
    value: int = 0
    minimum: int = 1
    maximum: int = 254

    direct: bool = False

    def pack(self) -> bytes:
        return ROW.pack(
            self.rule_id,
            self.source,
            self.source_ep,
            self.event,
            self.presses,
            self.target,
            self.target_ep,
            self.op | (0x80 if self.direct else 0),
            self.value,
            self.minimum,
            self.maximum,
        )


def encode(rules: list[Rule]) -> bytes:
    if len(rules) > MAX_RULES:
        raise ValueError("Too many rules")
    if len({r.rule_id for r in rules}) != len(rules):
        raise ValueError("Rule identifier collision")
    return (
        b"MRFX"
        + bytes([1, len(rules), 0, 0])
        + b"".join(rule.pack() for rule in sorted(rules, key=lambda r: r.rule_id))
    )


def digest(image: bytes) -> str:
    return sha256(image).hexdigest()
