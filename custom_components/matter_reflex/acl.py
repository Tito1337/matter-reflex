"""Reconcile fabric-scoped ACL lists, with a durable intent before each write."""

import json
from collections import Counter

from .transport import ReflexError

ACL = "0/31/0"
TARGET_LIMIT = "0/31/3"
ENTRY_LIMIT = "0/31/4"
FABRIC_INDEX = "0/62/5"


def fingerprint(entry):
    return json.dumps(entry, sort_keys=True, separators=(",", ":"))


def contents(entries):
    return Counter(map(fingerprint, entries))


def matches(entry, subject, endpoint, cluster, privilege):
    if entry["2"] != 2 or entry["1"] < privilege:
        return False
    if entry["3"] is not None and subject not in entry["3"]:
        return False
    targets = entry["4"]
    return targets is None or any(
        t.get("0") in (None, cluster)
        and t.get("1") in (None, endpoint)
        and t.get("2") is None
        for t in targets
    )


def plan(entries, owned, subject, needed, fabric, per_entry, max_entries, prune):
    """Return a complete replacement list and exact entries we own."""
    if not entries or not all(
        isinstance(e, dict)
        and e.get("254") == fabric
        and type(e.get("1")) is int
        and e["1"] in (1, 2, 3, 4, 5)
        and type(e.get("2")) is int
        and e["2"] in (1, 2, 3)
        and "3" in e
        and (e["3"] is None or isinstance(e["3"], list))
        and "4" in e
        and (e["4"] is None or isinstance(e["4"], list))
        for e in entries
    ):
        raise ReflexError("Incomplete or foreign ACL")
    # Keep this effective, fabric-wide CASE admin first even for a device
    # which processes a list in chunks and rejects a later entry.
    admin = entries[0]
    if not (
        admin["1"] == 5
        and admin["2"] == 2
        and admin["3"]
        and admin["4"] is None
        and subject not in admin["3"]
    ):
        raise ReflexError("Cannot verify the leading fabric administrator")
    # AccessControl.ACL requires Administer to read. A successful live CASE
    # read with a unique CASE administrator identifies HA's grant.
    if any(e["2"] == 2 and e["1"] == 5 for e in entries[1:]):
        raise ReflexError("Cannot identify HA among multiple CASE administrators")
    if any(
        e["2"] == 2 and e["1"] >= 4 and (e["3"] is None or subject in e["3"])
        for e in entries
    ):
        raise ReflexError("Reflex already holds excess privileges")
    if not all(type(x) is int and x > 0 for x in (per_entry, max_entries)):
        raise ReflexError("Invalid ACL capacity")
    if any(
        e.get("3") != [subject]
        or e.get("2") != 2
        or e.get("1") not in (1, 3)
        or e.get("254") != fabric
        or not e.get("4")
        for e in owned
    ):
        raise ReflexError("Invalid ownership journal for this fabric")
    known = set(map(fingerprint, owned))
    if any(count > 1 for key, count in contents(entries).items() if key in known):
        raise ReflexError("Ambiguous duplicated managed ACL")
    foreign = [e for e in entries if fingerprint(e) not in known]
    previous = [e for e in entries if fingerprint(e) in known]
    wanted = set(needed)
    if not prune:
        for e in previous:
            wanted.update((t["1"], t["0"], e["1"]) for t in e["4"])
    if any(
        (cluster, p) not in {(59, 1), (6, 3), (8, 3), (0x0102, 3)}
        or not 1 <= ep <= 65534
        for ep, cluster, p in wanted
    ):
        raise ReflexError("Unsupported ACL target or privilege")
    managed = []
    for privilege in (1, 3):
        targets = [
            {"0": c, "1": ep, "2": None}
            for ep, c, p in sorted(wanted)
            if p == privilege
            and not any(matches(e, subject, ep, c, p) for e in foreign)
        ]
        for start in range(0, len(targets), per_entry):
            managed.append(
                {
                    "1": privilege,
                    "2": 2,
                    "3": [subject],
                    "4": targets[start : start + per_entry],
                    "254": fabric,
                }
            )
    if len(foreign) + len(managed) > max_entries:
        raise ReflexError("Insufficient ACL entry capacity")
    return foreign + managed, managed


class AclManager:
    def __init__(self, store, subject):
        self.store = store
        self.subject = subject
        self.records = {}

    async def load(self):
        self.records = await self.store.async_load() or {}

    async def record(self, node, value):
        new = self.records.copy()
        if value:
            new[str(node)] = value
        else:
            new.pop(str(node), None)
        await self.store.async_save(new)
        self.records = new

    async def snapshot(self, client, node):
        paths = [ACL, FABRIC_INDEX, TARGET_LIMIT, ENTRY_LIMIT]
        if client.server_info.schema_version >= 14:
            report = await client.read(
                node, attribute_paths=paths, fabric_filtered=True
            )
            values = report.attributes
        else:
            values = await client.read_attribute(node, paths)
        if not all(path in values for path in paths):
            raise ReflexError(f"Missing ACL metadata on node {node}")
        rows, fabric = values[ACL], values[FABRIC_INDEX]
        if (
            type(fabric) is not int
            or not 1 <= fabric <= 254
            or not isinstance(rows, list)
            or any(
                not isinstance(e, dict) or type(e.get("254")) is not int for e in rows
            )
        ):
            raise ReflexError("Missing per-entry fabric index")
        return (
            [e for e in rows if e["254"] == fabric],
            fabric,
            values[TARGET_LIMIT],
            values[ENTRY_LIMIT],
        )

    async def reconcile(self, client, permissions, prune):
        nodes = set(permissions)
        if prune:
            # Keep the ledger for removed nodes, and continue cleaning up peers
            # which are still on the fabric.
            commissioned = {device.node_id for device in client.get_nodes()}
            nodes.update(set(map(int, self.records)) & commissioned)
        for node in sorted(nodes):
            if node == self.subject:
                raise ReflexError(
                    "Reflex cannot grant itself access to its control node"
                )
            await self._node(client, node, permissions.get(node, set()), prune)

    async def _node(self, client, node, permissions, prune):
        rows, fabric, per_entry, maximum = await self.snapshot(client, node)
        state = self.records.get(str(node), {})
        owned = state.get("owned", [])
        if pending := state.get("pending"):
            if contents(rows) == contents(pending["after"]):
                owned = pending["owned"]
            elif contents(rows) != contents(pending["before"]):
                raise ReflexError(
                    f"ACL changed during interrupted write on node {node}; inspect manually"
                )
            await self.record(node, {"owned": owned} if owned else None)
        after, new_owned = plan(
            rows, owned, self.subject, permissions, fabric, per_entry, maximum, prune
        )
        if contents(rows) != contents(after):
            await self.record(
                node,
                {
                    "owned": owned,
                    "pending": {"before": rows, "after": after, "owned": new_owned},
                },
            )
            # Store intent before the final live check. There is no disk I/O
            # between this check and submitting the whole-list write.
            confirm = await self.snapshot(client, node)
            if confirm != (rows, fabric, per_entry, maximum):
                await self.record(node, {"owned": owned} if owned else None)
                raise ReflexError(f"Concurrent ACL edit on node {node}")
            reply = await client.write_attribute(node, ACL, after)
            if not (
                isinstance(reply, list)
                and len(reply) == 1
                and reply[0].get("Status") == 0
            ):
                raise ReflexError(f"Device rejected ACL on node {node}")
            current, index, _, _ = await self.snapshot(client, node)
            if (
                index != fabric
                or not current
                or current[0] != rows[0]
                or contents(current) != contents(after)
            ):
                raise ReflexError(f"ACL readback mismatch on node {node}")
        if self.records.get(str(node), {}).get(
            "owned", []
        ) != new_owned or "pending" in self.records.get(str(node), {}):
            await self.record(node, {"owned": new_owned} if new_owned else None)
