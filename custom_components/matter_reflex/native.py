"""Matter control paths shared by configuration and runtime."""

import re

from chip.clusters import Objects as clusters
from matter_server.common.errors import InvalidArguments, InvalidCommand, MatterError

from .const import CLUSTER, ENDPOINT, LEASE_SECONDS, VENDOR
from .transport import ReflexError
from .wire import digest

LEASE_ACTION_ID = 1
PREFIX = f"{ENDPOINT}/{CLUSTER}/"
READY_PATH = PREFIX + "9"
SOFTWARE_VERSION_PATH = "0/40/9"
LEASE_IDENTITY_PATHS = ["0/40/2", "0/40/3", SOFTWARE_VERSION_PATH]
# UnsupportedAccess, UnsupportedEndpoint, UnsupportedCommand, UnsupportedAttribute,
# UnsupportedWrite, NotFound, and UnsupportedCluster from the Interaction Model status table.
UNSUPPORTED_LEASE_STATUSES = {0x7E, 0x7F, 0x81, 0x86, 0x88, 0x8B, 0xC3}


class LeaseUnsupported(ReflexError):
    """The board and controller cannot exchange a quiet lease renewal."""


async def supports_lease_action(client, node_id):
    """Validate the live product and firmware before selecting its lease operation."""
    values = await client.read_attribute(node_id, LEASE_IDENTITY_PATHS)
    if not isinstance(values, dict) or (
        values.get("0/40/2") != VENDOR or values.get("0/40/3") != "Matter Reflex"
    ):
        raise LeaseUnsupported(
            "Matter Server could not verify the Reflex board identity."
        )
    version = values.get(SOFTWARE_VERSION_PATH)
    if type(version) is not int or not 0 <= version <= 0xFFFFFFFF:
        raise LeaseUnsupported(
            "Matter Server returned no valid SoftwareVersion for the board."
        )
    return version >= 5


async def renew(client, node_id, use_action):
    """Send one untimed unicast interaction on the established Matter session."""
    try:
        if use_action:
            await client.send_device_command(
                node_id,
                ENDPOINT,
                clusters.Actions.Commands.InstantAction(actionID=LEASE_ACTION_ID),
                interaction_timeout_ms=3000,
            )
            return
        # Older firmware exposes a writable Lease attribute.
        result = await client.write_attribute(node_id, PREFIX + "0", LEASE_SECONDS)
    except (InvalidArguments, InvalidCommand) as err:
        raise LeaseUnsupported("Matter Server rejected the lease interaction.") from err
    except MatterError as err:
        if getattr(err, "status", None) in UNSUPPORTED_LEASE_STATUSES:
            raise LeaseUnsupported(
                f"The board rejected the lease (status {err.status:#x})."
            ) from err
        raise
    if not (
        isinstance(result, list)
        and len(result) == 1
        and isinstance(result[0], dict)
        and type(result[0].get("Status")) is int
    ):
        raise LeaseUnsupported("Matter Server did not confirm the Reflex lease write.")
    status = result[0]["Status"]
    if status in UNSUPPORTED_LEASE_STATUSES:
        raise LeaseUnsupported(
            f"The board rejected the lease write (status {status:#x})."
        )
    if status:
        raise ReflexError(f"The lease write failed (status {status:#x}).")


STATUS_PATHS = {
    PREFIX + str(i): key for i, key in ((2, "hash"), (3, "active"), (4, "executions"))
}


def validate_status_value(key, value):
    if key == "hash":
        valid = isinstance(value, str) and re.fullmatch("(?:[0-9a-f]{64})?", value)
    elif key == "active":
        valid = type(value) is bool
    else:
        valid = type(value) is int and 0 <= value <= 0xFFFFFFFF
    if not valid:
        raise ReflexError(f"Invalid Reflex status attribute {key}")


async def read_status(client, node_id, *, include_ready=False):
    paths = list(STATUS_PATHS)
    if include_ready:
        paths.append(READY_PATH)
    data = await client.read_attribute(node_id, paths)
    if not isinstance(data, dict):
        raise ReflexError("Matter Server returned an invalid status snapshot")
    status = {"node_id": f"{node_id:016x}"}
    for path, key in STATUS_PATHS.items():
        value = data.get(path)
        validate_status_value(key, value)
        status[key] = value
    if include_ready and data.get(READY_PATH) is not None:
        validate_status_value("ready", data[READY_PATH])
        status["ready"] = data[READY_PATH]
    return status


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
