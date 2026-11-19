"""Preserve commissioning data when a standalone XIAO gains OTA slots."""

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def partitions(path):
    with path.open() as source:
        rows = csv.reader(
            line for line in source if line.strip() and not line.startswith("#")
        )
        return {
            row[0].strip(): (
                row[1].strip(),
                row[2].strip(),
                int(row[3], 0),
                int(row[4], 0),
            )
            for row in rows
        }


def test_commissioned_ota_partition_layout():
    legacy = partitions(ROOT / "firmware/partitions.csv")
    ota = partitions(ROOT / "examples/partitions-ota.csv")
    for name, partition in legacy.items():
        if partition[0] == "data":
            assert ota[name] == partition

    _, _, start, size = legacy["factory"]
    assert ota["app0"][:3] == ("app", "ota_0", start)
    assert ota["app1"][:2] == ("app", "ota_1")
    assert ota["app0"][3] == ota["app1"][3]
    assert ota["otadata"][:2] == ("data", "ota")
    assert ota["otadata"][3] == 0x2000
    end = start
    for name in ("app0", "app1", "otadata"):
        _, _, offset, length = ota[name]
        assert offset >= end
        assert offset % (0x1000 if name == "otadata" else 0x10000) == 0
        end = offset + length
    assert end <= start + size
