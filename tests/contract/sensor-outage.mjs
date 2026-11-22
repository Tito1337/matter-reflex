// Two commissioned peers, real subscription reports and invocations on a virtual network.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { EventEmitter, once } from "node:events";
import { setTimeout as delay } from "node:timers/promises";
import {
    Crypto, Endpoint, Entropy, Environment, Logger, MockStorageService,
    Network, NetworkSimulator, Seconds, ServerNode,
} from "@matter/main";
import { OccupancySensingServer, WindowCoveringServer } from "@matter/main/behaviors";
import {
    ContactSensorDevice, DimmableLightDevice, HumiditySensorDevice, OccupancySensorDevice,
    OnOffPlugInUnitDevice, TemperatureSensorDevice, WaterLeakDetectorDevice, WindowCoveringDevice,
} from "@matter/main/devices";
import { createControllerNode, ControllerCommandHandler } from "@matter-server/ws-controller";

Logger.level = "error";
const mesh = new NetworkSimulator();
function host(id) {
    const env = new Environment("host-" + id);
    env.set(Crypto, Environment.default.get(Crypto));
    env.set(Entropy, env.get(Crypto));
    new MockStorageService(env);
    env.set(Network, mesh.addHost(id));
    return env;
}
function device(id) {
    return ServerNode.create({
        id: "device-" + id, environment: host(id),
        commissioning: { passcode: 20202021, discriminator: 3800 + id },
    });
}
const controller = await createControllerNode({
    id: "reflex", environment: host(1), adminFabricLabel: "Reflex outage test",
    serverVersion: "1.5.5", enableOtaProvider: false,
    dcl: { acceptTestCertificates: true, offline: true },
});
const handler = new ControllerCommandHandler(controller, { disableAutoSubscription: true });
const source = await device(2), target = await device(3);
const engine = spawn("/fixture/engine", ["/fixture/image"], { stdio: ["pipe", "pipe", "inherit"] });
const replies = createInterface({ input: engine.stdout })[Symbol.asyncIterator]();
async function step(line) {
    engine.stdin.write(line + "\n");
    const reply = await replies.next();
    assert.equal(reply.done, false, "firmware engine exited");
    return reply.value;
}

let subscription;
try {
    const sensors = [];
    for (const [type, id, attributes] of [
        [ContactSensorDevice, "door", { booleanState: { stateValue: true } }],
        [OccupancySensorDevice.with(OccupancySensingServer.with("PassiveInfrared")), "motion",
            { occupancySensing: { occupancy: { occupied: false } } }],
        [WaterLeakDetectorDevice, "leak", { booleanState: { stateValue: false } }],
        [TemperatureSensorDevice, "temperature", { temperatureMeasurement: { measuredValue: 100 } }],
        [HumiditySensorDevice, "humidity", { relativeHumidityMeasurement: { measuredValue: 5400 } }],
    ]) {
        sensors.push(await source.add(new Endpoint(type, { id, number: sensors.length + 1, ...attributes })));
    }
    const lamp = await target.add(new Endpoint(DimmableLightDevice, { id: "lamp", number: 1, levelControl: { currentLevel: 1 } }));
    const plug = await target.add(new Endpoint(OnOffPlugInUnitDevice, { id: "plug", number: 2 }));
    const shade = await target.add(new Endpoint(WindowCoveringDevice.with(
        WindowCoveringServer.with("Tilt", "PositionAwareTilt")), {
        id: "shade", number: 3, windowCovering: { type: 7, endProductType: 9, currentPositionTiltPercent100ths: 0 },
    }));
    await handler.start();
    async function commission(node, id) {
        await node.start();
        const peer = await controller.node.peers.commission({
            passcode: 20202021, discriminator: 3800 + id, timeout: Seconds(12), autoSubscribe: false,
        });
        return peer.peerAddress.nodeId;
    }
    const sourceId = await commission(source, 2);
    const targetId = await commission(target, 3);
    await handler.initializeNodes();
    const paths = ["1/69/0", "2/1030/0", "3/69/0", "4/1026/0", "5/1029/0"];
    const reports = new EventEmitter();
    async function subscribe() {
        await step("reset");
        subscription = await handler.subscribe({
            nodeId: sourceId, attributePaths: paths, minInterval: 0, maxInterval: 60,
        }, { report: r => reports.emit("update", r), closed() {} });
        assert.deepEqual(Object.keys(subscription.priming.attributes).sort(), [...paths].sort());
        await processReport(subscription.priming, 0);
    }
    let now = 0;
    async function processReport(report, expected) {
        let count = 0;
        for (const [path, value] of Object.entries(report.attributes)) {
            const [ep, cluster] = path.split("/");
            const response = await step(`report ${now} 21 ${ep} ${cluster} ${value === null ? -2147483648 : Number(value)}`);
            if (!response) continue;
            const [node, endpointId, op, operand, transition] = response.split(" ").map(Number);
            assert.equal(node, 42);
            let commandName, clusterId, data = {};
            if (op === 3) {
                commandName = "MoveToLevelWithOnOff";
                clusterId = 8;
                data = { level: operand, transitionTime: transition, optionsMask: 0, optionsOverride: 0 };
            } else if (op === 10) {
                commandName = "GoToTiltPercentage";
                clusterId = 258;
                data = { tiltPercent100thsValue: operand };
            } else {
                assert.ok(op === 0 || op === 1);
                commandName = op ? "On" : "Off";
                clusterId = 6;
            }
            await handler.handleInvoke({ nodeId: targetId, endpointId, clusterId, commandName, data });
            count++;
        }
        assert.equal(count, expected);
    }
    async function change(index, attributes, expected) {
        const pending = once(reports, "update", { signal: AbortSignal.timeout(5000) });
        await sensors[index].set(attributes);
        const [report] = await pending;
        assert.ok(Object.hasOwn(report.attributes, paths[index]));
        await processReport(report, expected);
        // Re-delivering the exact same report must never issue a second command.
        await processReport(report, 0);
    }
    await subscribe();
    await step("renew 0");
    await step("renew 4000000"); // Last HA renewal. Next deadline is at 14 seconds.
    // The leak automation is ESP-first even with a healthy lease.
    await change(2, { booleanState: { stateValue: true } }, 1);
    assert.equal(plug.state.onOff.onOff, true);
    await change(2, { booleanState: { stateValue: false } }, 0);
    now = 13999999;
    await change(0, { booleanState: { stateValue: false } }, 0);
    assert.equal(lamp.state.onOff.onOff, false);
    await change(0, { booleanState: { stateValue: true } }, 0);
    now = 14000000;
    await change(0, { booleanState: { stateValue: false } }, 1);
    assert.equal(lamp.state.onOff.onOff, true);
    subscription.close();
    await subscribe(); // Reconnect with door already open and lease expired.
    await change(1, { occupancySensing: { occupancy: { occupied: true } } }, 1);
    await delay(350);
    assert.equal(lamp.state.levelControl.currentLevel, 199);
    await change(2, { booleanState: { stateValue: true } }, 1);
    assert.equal(plug.state.onOff.onOff, true);
    await change(3, { temperatureMeasurement: { measuredValue: 0 } }, 0);
    await change(3, { temperatureMeasurement: { measuredValue: -1 } }, 1);
    assert.equal(shade.state.windowCovering.targetPositionTiltPercent100ths, 7500);
    await change(4, { relativeHumidityMeasurement: { measuredValue: 5500 } }, 0);
    await change(4, { relativeHumidityMeasurement: { measuredValue: 5501 } }, 1);
    assert.equal(lamp.state.onOff.onOff, false);
    await step("renew 14000000");
    await change(0, { booleanState: { stateValue: true } }, 0);
    await change(0, { booleanState: { stateValue: false } }, 0);
    console.log("Five subscribed sensor types passed, including door opens during outage → light on.");
} finally {
    engine.stdin.end();
    subscription?.close();
    await handler.close();
    await controller.close();
    await source.close();
    await target.close();
}
