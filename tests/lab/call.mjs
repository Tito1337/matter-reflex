import { createConnection } from "node:net";

const client = createConnection("/tmp/reflex-lab.sock");
client.setTimeout(8000, () => client.destroy(new Error("Fixture timed out")));
client.on("connect", () => client.write(process.argv[2] + "\n"));
client.pipe(process.stdout);
client.on("error", error => { console.error(error.message); process.exitCode = 1; });
