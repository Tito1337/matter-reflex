// Run inside the pinned Matter Server container with its network disabled.
import assert from "node:assert/strict";
import {
    Crypto, Endpoint, Entropy, Environment, Logger,
    MockStorageService, Network, NetworkSimulator, Seconds, ServerNode,
} from "@matter/main";
import { ActionsServer } from "@matter/main/behaviors";
import { OnOffLightSwitchDevice } from "@matter/main/devices";
import { createControllerNode, ControllerCommandHandler } from "@matter-server/ws-controller";
import { MessageCodec, NodeSession } from "@matter/protocol";

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

// This peer exercises the controller and the wire protocol. The ESP32-C6
// implements the action separately and is tested by the firmware build.
let accepted = 0;
class LeaseAction extends ActionsServer {
    instantAction({ actionId }) {
        assert.equal(actionId, 1);
        accepted++;
    }
}

const device = await ServerNode.create({
    id: "wire-device",
    environment: host(2),
    commissioning: { passcode: 20202021, discriminator: 3840 },
});
const controller = await createControllerNode({
    id: "wire-controller",
    environment: host(1),
    adminFabricLabel: "Reflex packet test",
    serverVersion: "1.5.5",
    enableOtaProvider: false,
    dcl: { acceptTestCertificates: true, offline: true },
});
const handler = new ControllerCommandHandler(controller, {});
const originalEncode = NodeSession.prototype.encode;
try {
    await device.add(new Endpoint(OnOffLightSwitchDevice.with(LeaseAction), {
        id: "reflex",
        actions: {
            actionList: [{
                actionId: 1, name: "Renew Reflex lease", type: 0, endpointListId: 1,
                supportedCommands: { instantAction: true }, state: 0,
            }],
            endpointLists: [{ endpointListId: 1, name: "Reflex", type: 0, endpoints: [1] }],
        },
    }));
    await device.start();
    await handler.start();
    const peer = await controller.node.peers.commission({
        passcode: 20202021, discriminator: 3840, timeout: Seconds(12), autoSubscribe: false,
    });
    await handler.initializeNodes();
    // Relate encrypted datagrams to the InvokeRequest without counting separate
    // reliable-message acknowledgements or background traffic as lease requests.
    const messageIds = new Set();
    let responses = 0;
    NodeSession.prototype.encode = function (message) {
        if (message.payloadHeader.protocolId === 1 && message.payloadHeader.messageType === 8) {
            messageIds.add(message.packetHeader.messageId);
        }
        if (message.payloadHeader.protocolId === 1 && message.payloadHeader.messageType === 9) responses++;
        return originalEncode.call(this, message);
    };
    const sizes = [];
    mesh.router.intercept((packet, forward) => {
        if (packet.kind === "udp" && packet.sourceAddress === "abcd::1" && packet.destAddress === "abcd::2") {
            const header = MessageCodec.decodePacket(packet.payload).header;
            if (messageIds.has(header.messageId)) sizes.push(packet.payload.byteLength);
        }
        forward(packet);
    });
    for (let attempt = 0; attempt < 2; attempt++) {
        sizes.length = 0;
        responses = 0;
        // Home Assistant's Python client uses these field names, including
        // null for the optional InvokeID. The stock dispatcher translates them.
        await handler.handleInvoke({
            nodeId: peer.peerAddress.nodeId, endpointId: 1, clusterId: 0x25,
            commandName: "InstantAction", data: { actionID: 1, invokeID: null },
            interactionTimeoutMs: 3000,
        });
        assert.equal(responses, 1);
        assert.equal(accepted, attempt + 1);
        assert.equal(sizes.length, 1, "each request must fit in one UDP datagram");
        // A piggybacked acknowledgement can add four bytes to the header.
        assert.ok(sizes[0] === 78 || sizes[0] === 82, "unexpected renewal size");
        console.log("Lease request UDP payload:", sizes[0], "bytes");
    }
} finally {
    NodeSession.prototype.encode = originalEncode;
    await handler.close();
    await controller.close();
    await device.close();
}
