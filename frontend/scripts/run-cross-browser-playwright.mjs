import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  createCrossBrowserPrefix,
  createNetworkGuardControlToken,
  releaseReservedPort,
  reserveAvailablePort,
  startLoopbackOnlyProxy,
  verifyPhase7BuildContract,
} from "./cross-browser-runner-core.mjs";


const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const frontendDirectory = resolve(scriptDirectory, "..");
const cliPath = resolve(frontendDirectory, "node_modules/@playwright/test/cli.js");
const rawArguments = process.argv.slice(2);
const useExistingBuild = rawArguments.includes("--use-existing-build");
const playwrightArguments = rawArguments.filter((argument) => argument !== "--use-existing-build");
if (useExistingBuild) {
  verifyPhase7BuildContract(frontendDirectory);
} else {
  const build = spawnSync(
    process.execPath,
    [resolve(scriptDirectory, "build-phase7-production.mjs")],
    { cwd: frontendDirectory, env: process.env, stdio: "inherit" },
  );
  if (build.error || build.status !== 0) {
    console.error(build.error
      ? `Unable to build phase7 production frontend: ${build.error.message}`
      : "Phase7 production frontend build failed.");
    process.exit(build.status || 1);
  }
  verifyPhase7BuildContract(frontendDirectory);
}
const port = await reserveAvailablePort();
const productionOrigin = `http://127.0.0.1:${port}`;
const networkProxyControlToken = createNetworkGuardControlToken();
const networkProxy = await startLoopbackOnlyProxy({
  initialAllowedOrigins: [productionOrigin],
  controlToken: networkProxyControlToken,
});
let networkProxyClosePromise;
const closeNetworkProxy = () => {
  if (!networkProxyClosePromise) {
    networkProxyClosePromise = networkProxy.close();
  }
  return networkProxyClosePromise;
};
let portReleased = false;
const releasePort = () => {
  if (portReleased) {
    return;
  }
  portReleased = true;
  releaseReservedPort(port);
};
const prefix = createCrossBrowserPrefix();
const outputDirectory = resolve(
  frontendDirectory,
  "..",
  "tmp",
  `playwright-phase7-${prefix}-${port}`,
);
mkdirSync(outputDirectory, { recursive: true });
const serviceWorkerFixtureDirectory = resolve(
  frontendDirectory,
  "dist",
  "network-guard-fixture",
);
const serviceWorkerFixturePath = resolve(
  serviceWorkerFixtureDirectory,
  "service-worker.js",
);
const serviceWorkerFixturePagePath = resolve(
  serviceWorkerFixtureDirectory,
  "index.html",
);
mkdirSync(serviceWorkerFixtureDirectory, { recursive: true });
writeFileSync(
  serviceWorkerFixturePagePath,
  "<!doctype html><html><head><meta charset=\"utf-8\"><title>Network guard fixture</title></head><body></body></html>\n",
  { encoding: "utf8", mode: 0o600 },
);
writeFileSync(
  serviceWorkerFixturePath,
  [
    'self.addEventListener("install", () => self.skipWaiting());',
    'self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));',
    'self.addEventListener("message", async (event) => {',
    "  try {",
    '    const target = event.data?.url || "http://elvern-guard-test.invalid/service-worker-probe?token=hidden";',
    "    await fetch(target);",
    "    event.source.postMessage({ blocked: false });",
    "  } catch {",
    "    event.source.postMessage({ blocked: true });",
    "  }",
    "});",
    "",
  ].join("\n"),
  { encoding: "utf8", mode: 0o600 },
);
verifyPhase7BuildContract(frontendDirectory);
let serviceWorkerFixtureRemoved = false;
const removeServiceWorkerFixture = () => {
  if (serviceWorkerFixtureRemoved) {
    return;
  }
  serviceWorkerFixtureRemoved = true;
  rmSync(serviceWorkerFixtureDirectory, { recursive: true, force: true });
  verifyPhase7BuildContract(frontendDirectory);
};
const cleanupLocalResources = () => {
  removeServiceWorkerFixture();
  releasePort();
};
process.once("exit", cleanupLocalResources);
process.once("SIGINT", () => {
  cleanupLocalResources();
  process.exit(130);
});
process.once("SIGTERM", () => {
  cleanupLocalResources();
  process.exit(143);
});

console.log(`Cross-browser Playwright: port=${port} prefix=${prefix}`);
console.log(`External-network authority: loopback proxy port=${networkProxy.port}`);
console.log(`Cross-browser output: ${outputDirectory}`);

const child = spawn(
  process.execPath,
  [cliPath, "test", "--config", "playwright.cross-browser.config.js", ...playwrightArguments],
  {
    cwd: frontendDirectory,
    env: {
      ...process.env,
      VITE_ELVERN_LIBRARY_SUMMARY_V2_MODE: "on",
      VITE_ELVERN_LIBRARY_REVISION_MODE: "on",
      ELVERN_PHASE7_BROWSER_PORT: String(port),
      ELVERN_PHASE7_BROWSER_PREFIX: prefix,
      ELVERN_PHASE7_BROWSER_OUTPUT_DIR: outputDirectory,
      ELVERN_PHASE7_NETWORK_PROXY: `http://127.0.0.1:${networkProxy.port}`,
      ELVERN_PHASE7_NETWORK_PROXY_CONTROL: `http://127.0.0.1:${networkProxy.port}`,
      ELVERN_PHASE7_NETWORK_PROXY_CONTROL_TOKEN: networkProxyControlToken,
    },
    stdio: "inherit",
  },
);

child.once("error", async (error) => {
  removeServiceWorkerFixture();
  releasePort();
  await closeNetworkProxy();
  console.error(`Unable to start Playwright: ${error.message}`);
  process.exitCode = 1;
});
child.once("exit", async (code, signal) => {
  removeServiceWorkerFixture();
  releasePort();
  const unexpectedAttempts = [...networkProxy.attempts];
  await closeNetworkProxy();
  if (signal) {
    console.error(`Playwright stopped by signal ${signal}.`);
    process.exitCode = 1;
    return;
  }
  if (unexpectedAttempts.length) {
    console.error(
      `External-network authority blocked ${unexpectedAttempts.length} unexpected attempt(s): `
      + JSON.stringify(unexpectedAttempts),
    );
    process.exitCode = 1;
    return;
  }
  process.exitCode = Number(code || 0);
});
