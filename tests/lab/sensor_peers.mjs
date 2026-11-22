// Extra endpoints for the disposable HA fabric. The persistent button lab keeps its original layout.
import { Endpoint } from "@matter/main";
import { LevelControlServer, OccupancySensingServer, OnOffServer, WindowCoveringServer } from "@matter/main/behaviors";
import { ContactSensorDevice, DimmableLightDevice, HumiditySensorDevice, OccupancySensorDevice,
    OnOffPlugInUnitDevice, TemperatureSensorDevice, WaterLeakDetectorDevice, WindowCoveringDevice } from "@matter/main/devices";

export async function addSensors(node) {
    const inputs = new Map();
    async function add(type, name, number, attribute, baseline, triggered) {
        const endpoint = await node.add(new Endpoint(type, { id: name, number, ...attribute(baseline) }));
        inputs.set(name, { baseline, triggered, set: value => endpoint.set(attribute(value)) });
    }
    await add(ContactSensorDevice, "contact", 2, v => ({ booleanState: { stateValue: v } }), true, false);
    await add(OccupancySensorDevice.with(OccupancySensingServer.with("PassiveInfrared")), "occupancy", 3,
        v => ({ occupancySensing: { occupancy: { occupied: v } } }), false, true);
    await add(WaterLeakDetectorDevice, "leak", 4, v => ({ booleanState: { stateValue: v } }), false, true);
    await add(TemperatureSensorDevice, "temperature", 5, v => ({ temperatureMeasurement: { measuredValue: v } }), 0, -100);
    await add(HumiditySensorDevice, "humidity", 6, v => ({ relativeHumidityMeasurement: { measuredValue: v } }), 5500, 5600);
    return async function change(name) {
        if (name === "baseline") {
            for (const input of inputs.values()) await input.set(input.baseline);
        } else if (name === "same-side") {
            // New reports, still below freezing and above the humidity threshold.
            await inputs.get("temperature").set(-200);
            await inputs.get("humidity").set(5700);
        } else {
            const input = inputs.get(name);
            if (!input) throw new Error("Unknown lab sensor " + name);
            await input.set(input.triggered);
        }
    };
}

export async function addSensorTargets(node) {
    const commands = [];
    function record(context, command, fields = {}) {
        const peer = context.session?.peerAddress;
        if (peer) commands.push({ sender: String(peer.nodeId), command, ...fields });
    }
    class Outlet extends OnOffServer {
        on() { record(this.context, "plugOn"); return super.on(); }
        off() { record(this.context, "plugOff"); return super.off(); }
        // The SDK dispatches Toggle through on()/off(). Mark it so it cannot pass
        // as the absolute command expected by either plug automation.
        toggle() { record(this.context, "plugToggle"); return super.toggle(); }
    }
    class Dimmer extends LevelControlServer.with("OnOff", "Lighting") {
        moveToLevelWithOnOff(request) {
            record(this.context, "level", { level: request.level, transition: request.transitionTime });
            return super.moveToLevelWithOnOff(request);
        }
    }
    class Shade extends WindowCoveringServer.with("Lift", "PositionAwareLift") {
        goToLiftPercentage(request) {
            record(this.context, "lift", { value: request.liftPercent100thsValue });
            return super.goToLiftPercentage(request);
        }
    }
    const plug = await node.add(new Endpoint(OnOffPlugInUnitDevice.with(Outlet), { id: "outlet", number: 3 }));
    const dimmer = await node.add(new Endpoint(DimmableLightDevice.with(Dimmer), {
        id: "dimmer", number: 4, levelControl: { currentLevel: 1 },
    }));
    const shade = await node.add(new Endpoint(WindowCoveringDevice.with(Shade), {
        id: "lift", number: 5, windowCovering: { type: 0, endProductType: 0, currentPositionLiftPercent100ths: 0 },
    }));
    return {
        snapshot() {
            return { commands: [...commands], plug: plug.state.onOff.onOff, on: dimmer.state.onOff.onOff,
                level: dimmer.state.levelControl.currentLevel, lift: shade.state.windowCovering.targetPositionLiftPercent100ths };
        },
        async reset() {
            await plug.set({ onOff: { onOff: false } });
            await dimmer.set({ onOff: { onOff: false }, levelControl: { currentLevel: 1 } });
            await shade.set({ windowCovering: { targetPositionLiftPercent100ths: 0 } });
            commands.length = 0;
        },
    };
}
