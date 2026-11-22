// The same Matter endpoints run on the lab LAN and in the offline fixture check.
import { setTimeout as delay } from "node:timers/promises";
import { Endpoint, Millis } from "@matter/main";
import { OnOffServer, SwitchServer, WindowCoveringServer } from "@matter/main/behaviors";
import { GenericSwitchDevice, OnOffLightDevice, WindowCoveringDevice } from "@matter/main/devices";

export async function addButton(node) {
    const button = await node.add(new Endpoint(GenericSwitchDevice.with(SwitchServer.with(
        "MomentarySwitch", "MomentarySwitchRelease", "MomentarySwitchLongPress", "MomentarySwitchMultiPress",
    )), {
        id: "button", number: 1, switch: {
            numberOfPositions: 2, currentPosition: 0, multiPressMax: 2,
            longPressDelay: Millis(800), multiPressDelay: Millis(300),
        },
    }));
    return async function press(gesture) {
        if (gesture !== "double" && gesture !== "long") throw new Error("Expected double or long");
        for (let i = 0; i < (gesture === "double" ? 2 : 1); i++) {
            await button.set({ switch: { currentPosition: 1 } });
            await delay(gesture === "long" ? 1100 : 60);
            await button.set({ switch: { currentPosition: 0 } });
            await delay(60);
        }
        await delay(350); // Allow the SDK's multi-press timer to complete before the next gesture.
    };
}

export async function addTargets(node) {
    const commands = [];
    function record(context, command, value) {
        const address = context.session?.peerAddress;
        if (address) commands.push({ sender: String(address.nodeId), command, value });
    }
    class Lamp extends OnOffServer.with("Lighting") {
        on() {
            record(this.context, "on");
            return super.on();
        }
    }
    class Blind extends WindowCoveringServer.with("Tilt", "PositionAwareTilt") {
        goToTiltPercentage(request) {
            record(this.context, "tilt", request.tiltPercent100thsValue);
            return super.goToTiltPercentage(request);
        }
    }
    const lamp = await node.add(new Endpoint(OnOffLightDevice.with(Lamp), { id: "lamp", number: 1 }));
    const blind = await node.add(new Endpoint(WindowCoveringDevice.with(Blind), {
        id: "blind", number: 2, windowCovering: {
            type: 7, endProductType: 9, currentPositionTiltPercent100ths: 0,
        },
    }));
    return {
        snapshot() {
            return { commands: [...commands], on: lamp.state.onOff.onOff,
                tilt: blind.state.windowCovering.targetPositionTiltPercent100ths };
        },
        async reset() {
            await lamp.set({ onOff: { onOff: false } });
            await blind.set({ windowCovering: { targetPositionTiltPercent100ths: 0 } });
            commands.length = 0;
        },
    };
}
