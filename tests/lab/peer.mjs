import { randomInt } from "node:crypto";
import { createServer } from "node:net";
import { rm } from "node:fs/promises";
import { createInterface } from "node:readline";
import { Environment, Logger, ServerNode } from "@matter/main";
import { addButton, addTargets } from "./peers.mjs";
import { addSensors, addSensorTargets } from "./sensor_peers.mjs";

const socketPath = "/tmp/reflex-lab.sock";
const mode = process.argv[2];
if (!["button", "targets"].includes(mode)) throw new Error("Expected button or targets");
Logger.level = "error";
const button = mode === "button";
const env = Environment.default;
env.vars.set("storage.path", "/data");
env.vars.set("mdns.networkInterface", process.env.LAB_INTERFACE);
let passcode;
do { passcode = randomInt(1, 99999999); }
while (/^(\d)\1{7}$/.test(String(passcode)) || [12345678, 87654321].includes(passcode));
const node = await ServerNode.create({
    id: "reflex-lab-" + mode, environment: env, network: { port: button ? 5570 : 5571 },
    commissioning: { passcode, discriminator: button ? 3200 : 3500 },
    productDescription: { name: button ? "Reflex Lab Button" : "Reflex Lab Targets",
        deviceType: button ? 15 : 256 },
    basicInformation: { productName: button ? "Reflex Lab Button" : "Reflex Lab Targets",
        serialNumber: button ? "REFLEXLABBUTTON" : "REFLEXLABTARGETS" },
});
const press = button && await addButton(node);
const targets = !button && await addTargets(node);
const extended = process.argv[3] === "sensors";
const sensors = extended && button && await addSensors(node);
const sensorTargets = extended && !button && await addSensorTargets(node);
await node.start();
await rm(socketPath, { force: true });
let busy = false;
const server = createServer(async socket => {
    socket.on("error", () => {});
    for await (const command of createInterface({ input: socket })) {
        if (busy) { socket.end('{"error":"Fixture busy"}\n'); break; }
        busy = true;
        try {
            let result;
            if (command === "info") {
                const state = node.state.commissioning;
                const subscribers = Object.entries(node.state.sessions.sessions)
                    .filter(([, session]) => session.fabric && session.numberOfActiveSubscriptions > 0)
                    .map(([id, session]) => ({ id, node: String(session.peerNodeId) }));
                result = { commissioned: node.lifecycle.isCommissioned, subscribers,
                    fabrics: Object.keys(state.fabrics).length,
                    code: node.lifecycle.isCommissioned ? undefined : state.pairingCodes.manualPairingCode };
            } else if (button && ["double", "long"].includes(command)) await press(command);
            else if (!button && command === "snapshot") result = targets.snapshot();
            else if (!button && command === "reset") await targets.reset();
            else if (sensors && command.startsWith("sensor ")) await sensors(command.slice(7));
            else if (sensorTargets && command === "sensor-snapshot") result = sensorTargets.snapshot();
            else if (sensorTargets && command === "sensor-reset") await sensorTargets.reset();
            else throw new Error("Unknown fixture command");
            socket.end(JSON.stringify(result ?? { ok: true }) + "\n");
        } catch (error) {
            socket.end(JSON.stringify({ error: error.message }) + "\n");
        } finally { busy = false; }
        break;
    }
});
server.listen(socketPath);
async function stop() {
    server.close();
    await node.close();
    process.exit(0);
}
process.once("SIGTERM", stop);
process.once("SIGINT", stop);
