// Commission the actual lab endpoints offline. The controller here only checks the fixtures.
import assert from "node:assert/strict";
import { setTimeout as delay } from "node:timers/promises";
import { Crypto, Entropy, Environment, Logger, MockStorageService, Network,
    NetworkSimulator, Seconds, ServerNode } from "@matter/main";
import { createControllerNode, ControllerCommandHandler } from "@matter-server/ws-controller";
import { addButton, addTargets } from "./peers.mjs";

Logger.level = "error";
const mesh = new NetworkSimulator();
function host(id) {
    const env = new Environment("lab-host-" + id);
    env.set(Crypto, Environment.default.get(Crypto));
    env.set(Entropy, env.get(Crypto));
    new MockStorageService(env);
    env.set(Network, mesh.addHost(id));
    return env;
}
const controller = await createControllerNode({ id: "lab-controller", environment: host(1),
    adminFabricLabel: "Lab Smoke", serverVersion: "1.5.5", enableOtaProvider: false,
    dcl: { acceptTestCertificates: true, offline: true } });
const handler = new ControllerCommandHandler(controller, { disableAutoSubscription: true });
async function device(id) {
    return ServerNode.create({ id: "lab-" + id, environment: host(id),
        commissioning: { passcode: 20202021, discriminator: 3800 + id } });
}
async function commission(node, id) {
    await node.start();
    const peer = await controller.node.peers.commission({ passcode: 20202021,
        discriminator: 3800 + id, timeout: Seconds(12), autoSubscribe: false });
    return peer.peerAddress.nodeId;
}
const source = await device(2), target = await device(3);
let subscription;
try {
    const press = await addButton(source);
    const targets = await addTargets(target);
    await handler.start();
    const buttonId = await commission(source, 2);
    const targetId = await commission(target, 3);
    await handler.initializeNodes();
    const events = [];
    subscription = await handler.subscribe({ nodeId: buttonId,
        attributePaths: ["1/59/1"],
        eventPaths: [2, 6].map(id => ({ path: "1/59/" + id, isUrgent: true })),
        minInterval: 0, maxInterval: 5,
    }, { report: r => events.push(...r.events), closed() {} });
    await press("double");
    await press("long");
    for (let i = 0; i < 70 && events.length < 2; i++) await delay(100);
    assert.deepEqual(events.map(e => [e.event_id, e.data.totalNumberOfPressesCounted]), [[6, 2], [2, undefined]]);
    const on = { nodeId: targetId, endpointId: 1, clusterId: 6, commandName: "On", data: {} };
    await targets.reset();
    await handler.handleInvoke(on);
    await handler.handleInvoke(on); // Two invokes with identical end state must remain distinguishable.
    await handler.handleInvoke({ nodeId: targetId, endpointId: 2, clusterId: 258,
        commandName: "GoToTiltPercentage", data: { tiltPercent100thsValue: 2500 } });
    const sender = String(controller.fabric.nodeId);
    assert.deepEqual(targets.snapshot(), { commands: [
        { sender, command: "on", value: undefined }, { sender, command: "on", value: undefined },
        { sender, command: "tilt", value: 2500 }], on: true, tilt: 2500 });
    console.log("Commissioned peers, observed double and long presses and verified command counts, sender and blind tilt.");
} finally {
    subscription?.close();
    await handler.close();
    await controller.close();
    await source.close();
    await target.close();
}
