"""Commission lab peers and install their labeled rules through Home Assistant."""

import asyncio
import json
import os
import sys

import aiohttp


async def setup(base, codes):
    async with (
        aiohttp.ClientSession(
            headers={"Authorization": "Bearer " + os.environ["HA_TOKEN"]}
        ) as http,
        http.ws_connect(base + "/api/websocket") as ws,
    ):
        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": os.environ["HA_TOKEN"]})
        if (await ws.receive_json())["type"] != "auth_ok":
            raise RuntimeError("Home Assistant rejected the lab token")
        sequence = 0

        async def request(command, **fields):
            nonlocal sequence
            sequence += 1
            await ws.send_json({"type": command, "id": sequence, **fields})
            reply = await ws.receive_json(timeout=150)
            if reply.get("id") != sequence or not reply.get("success"):
                raise RuntimeError(
                    f"Home Assistant {command} failed: {reply.get('error')}"
                )
            return reply["result"]

        for code in codes:
            await request("matter/commission", code=code, network_only=True)

        # Match HA's interviewed nodes by the fixture serial numbers. Entity names can be renamed.
        for _ in range(90):
            devices = await request("config/device_registry/list")
            entities = await request("config/entity_registry/list")
            found = {}
            for serial, domain in (
                ("REFLEXLABBUTTON", "event"),
                ("REFLEXLABTARGETS", "light"),
                ("REFLEXLABTARGETS", "cover"),
            ):
                ids = {d["id"] for d in devices if d.get("serial_number") == serial}
                ids.update(d["id"] for d in devices if d.get("via_device_id") in ids)
                matches = [
                    e["entity_id"]
                    for e in entities
                    if e["platform"] == "matter"
                    and e.get("device_id") in ids
                    and e["entity_id"].startswith(domain + ".")
                    and not e.get("disabled_by")
                ]
                if len(matches) > 1:
                    raise RuntimeError(f"Ambiguous lab {domain} entities: {matches}")
                if matches:
                    found[domain] = matches[0]
            if len(found) == 3:
                break
            await asyncio.sleep(1)
        else:
            raise RuntimeError(
                f"HA interview incomplete. Found fixture entities: {found}"
            )

        labels = await request("config/label_registry/list")
        label = next((x for x in labels if x["name"].casefold() == "reflex"), None)
        if label is None:
            label = await request("config/label_registry/create", name="reflex")
        for gesture, service, target, data in (
            ("multi_press_2", "light.turn_on", found["light"], {}),
            (
                "long_press",
                "cover.set_cover_tilt_position",
                found["cover"],
                {"tilt_position": 75},
            ),
        ):
            rule_id = "matter_reflex_lab_" + gesture
            action = {"action": service, "target": {"entity_id": target}}
            if data:
                action["data"] = data
            rule = {
                "alias": "Matter Reflex Lab " + gesture,
                "mode": "single",
                "triggers": [
                    {
                        "trigger": "event.received",
                        "target": {"entity_id": found["event"]},
                        "options": {"event_type": [gesture]},
                    }
                ],
                "actions": [action],
            }
            async with http.post(
                base + "/api/config/automation/config/" + rule_id, json=rule
            ) as response:
                if response.status != 200:
                    raise RuntimeError(
                        f"Could not install {rule_id}: {await response.text()}"
                    )
            # HA saves the rule before its asynchronous registry reload completes.
            for _ in range(60):
                entities = await request("config/entity_registry/list")
                entry = next(
                    (
                        e
                        for e in entities
                        if e["platform"] == "automation" and e["unique_id"] == rule_id
                    ),
                    None,
                )
                if entry:
                    break
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError(f"HA did not register {rule_id}")
            await request(
                "config/entity_registry/update",
                entity_id=entry["entity_id"],
                labels=sorted({*entry.get("labels", []), label["label_id"]}),
            )
            print(f"Installed {entry['entity_id']} for {target}", flush=True)
        return found


if __name__ == "__main__":
    asyncio.run(setup(sys.argv[1].rstrip("/"), json.load(sys.stdin)))
