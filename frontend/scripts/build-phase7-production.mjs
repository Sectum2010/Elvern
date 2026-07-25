import { spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { PHASE7_BUILD_CONTRACT } from "./cross-browser-runner-core.mjs";


const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const frontendDirectory = resolve(scriptDirectory, "..");
const buildEnvironment = Object.fromEntries(
  Object.entries(process.env).filter(([key]) => !key.startsWith("VITE_ELVERN_")),
);
Object.assign(buildEnvironment, {
  VITE_ELVERN_LIBRARY_SUMMARY_V2_MODE: "on",
  VITE_ELVERN_LIBRARY_REVISION_MODE: "on",
});

const result = spawnSync("npm", ["run", "build"], {
  cwd: frontendDirectory,
  env: buildEnvironment,
  stdio: "inherit",
});
if (result.error) {
  console.error(`Unable to build phase7 production frontend: ${result.error.message}`);
  process.exit(1);
}
if (result.status !== 0) process.exit(result.status || 1);

const contractPath = resolve(frontendDirectory, "dist", ".elvern-build-contract.json");
mkdirSync(dirname(contractPath), { recursive: true });
writeFileSync(contractPath, `${JSON.stringify(PHASE7_BUILD_CONTRACT, null, 2)}\n`, {
  encoding: "utf8",
  mode: 0o644,
});
console.log(`Wrote phase7 build contract: ${contractPath}`);
