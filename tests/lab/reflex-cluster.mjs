// Register the firmware's vendor cluster before the disposable controller loads its model map.
// The lab standalone firmware transfers rules through encrypted Matter sessions.
import { AttributeModel, ClusterModel, EventModel, FieldModel, Matter } from "@matter/main/model";

const attributes = [
    [0, "Lease", "uint16", "RW"],
    [1, "Frame", "string", "RW"],
    [2, "Hash", "string", "R"],
    [3, "Active", "bool", "R"],
    [4, "Executions", "uint32", "R"],
    ...[5, 6, 7, 8].map(id => [id, `Journal${id - 5}`, "string", "R"]),
    [9, "Ready", "uint32", "R"],
].map(([id, name, type, access]) => new AttributeModel({ id, name, type, access, conformance: "M" }));

Matter.children.push(new ClusterModel({
    id: 0xfff1fc00, name: "MatterReflex", revision: 1,
    children: [...attributes, new EventModel({
        id: 0, name: "RuleExecuted", priority: "info", conformance: "M",
        children: [
            [0, "RuleId", "uint32"], [1, "NodeId", "uint64"],
            [2, "SourceEvent", "uint64"], [3, "Status", "uint16"],
        ].map(([id, name, type]) => new FieldModel({ id, name, type, conformance: "M" })),
    })],
}));
