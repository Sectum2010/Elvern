import { spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  PHASE7_BUILD_CONTRACT_FILENAME,
  createPhase7BuildContract,
  verifyPhase7BuildContract,
} from "./cross-browser-runner-core.mjs";


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

const contract = createPhase7BuildContract(frontendDirectory);
const contractPath = resolve(
  frontendDirectory,
  "dist",
  PHASE7_BUILD_CONTRACT_FILENAME,
);
mkdirSync(dirname(contractPath), { recursive: true });
writeFileSync(contractPath, `${JSON.stringify(contract, null, 2)}\n`, {
  encoding: "utf8",
  mode: 0o644,
});
verifyPhase7BuildContract(frontendDirectory);
console.log(`Wrote phase7 build contract: ${contractPath}`);
