import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  createCrossBrowserPrefix,
  releaseReservedPort,
  reserveAvailablePort,
} from "./cross-browser-runner-core.mjs";


const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const frontendDirectory = resolve(scriptDirectory, "..");
const cliPath = resolve(frontendDirectory, "node_modules/@playwright/test/cli.js");
const port = await reserveAvailablePort();
let portReleased = false;
const releasePort = () => {
  if (portReleased) {
    return;
  }
  portReleased = true;
  releaseReservedPort(port);
};
process.once("exit", releasePort);
process.once("SIGINT", () => {
  releasePort();
  process.exit(130);
});
process.once("SIGTERM", () => {
  releasePort();
  process.exit(143);
});
const prefix = createCrossBrowserPrefix();
const outputDirectory = resolve(
  frontendDirectory,
  "..",
  "tmp",
  `playwright-phase7-${prefix}-${port}`,
);
mkdirSync(outputDirectory, { recursive: true });

console.log(`Cross-browser Playwright: port=${port} prefix=${prefix}`);
console.log(`Cross-browser output: ${outputDirectory}`);

const child = spawn(
  process.execPath,
  [cliPath, "test", "--config", "playwright.cross-browser.config.js", ...process.argv.slice(2)],
  {
    cwd: frontendDirectory,
    env: {
      ...process.env,
      ELVERN_PHASE7_BROWSER_PORT: String(port),
      ELVERN_PHASE7_BROWSER_PREFIX: prefix,
      ELVERN_PHASE7_BROWSER_OUTPUT_DIR: outputDirectory,
    },
    stdio: "inherit",
  },
);

child.once("error", (error) => {
  console.error(`Unable to start Playwright: ${error.message}`);
  process.exitCode = 1;
});
child.once("exit", (code, signal) => {
  releasePort();
  if (signal) {
    console.error(`Playwright stopped by signal ${signal}.`);
    process.exitCode = 1;
    return;
  }
  process.exitCode = Number(code || 0);
});
