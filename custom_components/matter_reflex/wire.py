"""Bounded rule images. Integers are big-endian and node IDs never pass through a float."""

from dataclasses import dataclass
from hashlib import sha256
from struct import Struct

MAX_RULES = 32
ROW = Struct(">IQHBBQHBhBBx")
EXTENSION = Struct(">hH")  # threshold in hundredths, transition in tenths


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

    threshold: int = 0
    transition: int = 0

    def pack(self, version=1) -> bytes:
        return ROW.pack(
            self.rule_id,
            self.source,
            self.source_ep,
            self.event,
            self.presses,
            self.target,
            self.target_ep,
            self.op,
            self.value,
            self.minimum,
            self.maximum,
        ) + (EXTENSION.pack(self.threshold, self.transition) if version == 2 else b"")


def encode(rules: list[Rule]) -> bytes:
    if len(rules) > MAX_RULES:
        raise ValueError("Too many rules")
    if len({r.rule_id for r in rules}) != len(rules):
        raise ValueError("Rule identifier collision")
    version = 2 if any(r.event > 6 or r.op > 9 or r.transition for r in rules) else 1
    return (
        b"MRFX"
        + bytes([version, len(rules), 0, 0])
        + b"".join(
            rule.pack(version) for rule in sorted(rules, key=lambda r: r.rule_id)
        )
    )


def digest(image: bytes) -> str:
    return sha256(image).hexdigest()
