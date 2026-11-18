"""Matter control paths shared by configuration and runtime."""

import re

from chip.clusters import Objects as clusters

from .const import CLUSTER, ENDPOINT
from .transport import ReflexError
from .wire import digest

LEASE_ENDPOINT = 2
PREFIX = f"{ENDPOINT}/{CLUSTER}/"


async def renew(client, node_id, seconds):
    """Keep the logical HA-present output on for the next lease interval."""
    await client.send_device_command(
        node_id,
        LEASE_ENDPOINT,
        clusters.OnOff.Commands.OnWithTimedOff(
            onOffControl=0, onTime=seconds * 10, offWaitTime=0
        ),
        interaction_timeout_ms=3000,
    )


async def read_status(client, node_id):
    paths = [PREFIX + str(i) for i in (2, 3, 4)]
    data = await client.read_attribute(node_id, paths)
    value, active, count = (data.get(p) for p in paths)
    if (
        not isinstance(value, str)
        or not re.fullmatch("(?:[0-9a-f]{64})?", value)
        or type(active) is not bool
        or type(count) is not int
        or not 0 <= count <= 0xFFFFFFFF
    ):
        raise ReflexError("Server cannot read the Reflex status attributes")
    return {
        "node_id": f"{node_id:016x}",
        "hash": value,
        "active": active,
        "executions": count,
    }


async def write_frame(client, node_id, frame):
    result = await client.write_attribute(node_id, PREFIX + "1", frame)
    if not (
        isinstance(result, list) and len(result) == 1 and result[0].get("Status") == 0
    ):
        raise ReflexError("Server rejected the manufacturer-specific attribute write")


def frames(image):
    hash_hex = digest(image)
    yield f"B{hash_hex}{len(image):04x}"
    for offset in range(0, len(image), 96):
        yield f"D{offset:04x}{image[offset : offset + 96].hex()}"
    yield f"C{hash_hex}"


async def probe(client, node_id):
    await read_status(client, node_id)
    # Begin an empty transfer without committing it. The installed image stays active.
    await write_frame(client, node_id, next(frames(b"MRFX\x01\x00\x00\x00")))
