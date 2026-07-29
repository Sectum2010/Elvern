import { spawn, spawnSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  createCrossBrowserPrefix,
  createNetworkGuardControlToken,
  releaseReservedPort,
  reserveAvailablePort,
  startNetworkGuardFixtureServer,
  startLoopbackOnlyProxy,
  startTlsWebSocketFixtureServer,
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
const networkGuardFixture = await startNetworkGuardFixtureServer();
const networkProxyControlToken = createNetworkGuardControlToken();
const networkProxy = await startLoopbackOnlyProxy({
  initialAllowedOrigins: [productionOrigin, networkGuardFixture.origin],
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
const tlsKeyPath = resolve(outputDirectory, "network-guard-wss.key");
const tlsCertificatePath = resolve(outputDirectory, "network-guard-wss.crt");
const tlsCertificate = spawnSync("openssl", [
  "req",
  "-x509",
  "-newkey",
  "rsa:2048",
  "-nodes",
  "-keyout",
  tlsKeyPath,
  "-out",
  tlsCertificatePath,
  "-subj",
  "/CN=127.0.0.1",
  "-addext",
  "subjectAltName=IP:127.0.0.1",
  "-days",
  "1",
], {
  cwd: outputDirectory,
  encoding: "utf8",
});
if (tlsCertificate.error || tlsCertificate.status !== 0) {
  throw new Error(
    tlsCertificate.error
      ? `Unable to create local WSS fixture certificate: ${tlsCertificate.error.message}`
      : `Unable to create local WSS fixture certificate: ${tlsCertificate.stderr.trim()}`,
  );
}
const tlsWebSocketFixture = await startTlsWebSocketFixtureServer({
  certificatePath: tlsCertificatePath,
  keyPath: tlsKeyPath,
});
verifyPhase7BuildContract(frontendDirectory);
const cleanupLocalResources = () => {
  releasePort();
};
let child = null;
process.once("exit", cleanupLocalResources);
process.once("SIGINT", async () => {
  child?.kill("SIGINT");
  await Promise.all([
    closeNetworkProxy(),
    networkGuardFixture.close(),
    tlsWebSocketFixture.close(),
  ]);
  cleanupLocalResources();
  process.exit(130);
});
process.once("SIGTERM", async () => {
  child?.kill("SIGTERM");
  await Promise.all([
    closeNetworkProxy(),
    networkGuardFixture.close(),
    tlsWebSocketFixture.close(),
  ]);
  cleanupLocalResources();
  process.exit(143);
});

console.log(`Cross-browser Playwright: port=${port} prefix=${prefix}`);
console.log(`External-network authority: loopback proxy port=${networkProxy.port}`);
console.log(`Cross-browser output: ${outputDirectory}`);

child = spawn(
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
      ELVERN_PHASE7_SW_FIXTURE_ORIGIN: networkGuardFixture.origin,
      ELVERN_PHASE7_WSS_FIXTURE_ORIGIN: tlsWebSocketFixture.origin,
      ELVERN_PHASE7_HTTPS_FIXTURE_ORIGIN: tlsWebSocketFixture.httpsOrigin,
    },
    stdio: "inherit",
  },
);

child.once("error", async (error) => {
  releasePort();
  await Promise.all([
    closeNetworkProxy(),
    networkGuardFixture.close(),
    tlsWebSocketFixture.close(),
  ]);
  console.error(`Unable to start Playwright: ${error.message}`);
  process.exitCode = 1;
});
child.once("exit", async (code, signal) => {
  releasePort();
  const unexpectedAttempts = [...networkProxy.attempts];
  await Promise.all([
    closeNetworkProxy(),
    networkGuardFixture.close(),
    tlsWebSocketFixture.close(),
  ]);
  verifyPhase7BuildContract(frontendDirectory);
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
