import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { join, resolve } from "node:path";

import { afterEach, describe, expect, test } from "vitest";

import {
  classifyProxyTarget,
  createNetworkGuardControlToken,
  createPhase7BuildContract,
  PHASE7_BUILD_CONTRACT,
  startLoopbackOnlyProxy,
  verifyPhase7BuildContract,
} from "./cross-browser-runner-core.mjs";


const createdDirectories = [];


function frontendFixture() {
  const root = resolve(process.cwd(), "..", "tmp");
  mkdirSync(root, { recursive: true });
  const directory = mkdtempSync(join(root, "phase7-build-contract-"));
  createdDirectories.push(directory);
  mkdirSync(join(directory, "dist"));
  writeFileSync(join(directory, "dist", "index.html"), "<!doctype html>\n");
  mkdirSync(join(directory, "dist", "assets"));
  writeFileSync(join(directory, "dist", "assets", "app.js"), "console.log('app');\n");
  return directory;
}

function writeValidContract(frontendDirectory) {
  const contractPath = join(
    frontendDirectory,
    "dist",
    ".elvern-build-contract.json",
  );
  writeFileSync(
    contractPath,
    `${JSON.stringify(createPhase7BuildContract(frontendDirectory), null, 2)}\n`,
    { mode: 0o644 },
  );
  return contractPath;
}


afterEach(() => {
  while (createdDirectories.length) {
    rmSync(createdDirectories.pop(), { recursive: true, force: true });
  }
});


describe("phase7 production build contract", () => {
  test("rejects a missing or stale dist contract", () => {
    const frontendDirectory = frontendFixture();
    expect(() => verifyPhase7BuildContract(frontendDirectory)).toThrow(/build contract/i);
    writeFileSync(
      join(frontendDirectory, "dist", ".elvern-build-contract.json"),
      JSON.stringify({ ...PHASE7_BUILD_CONTRACT, library_revision_mode: "off" }),
      { mode: 0o644 },
    );
    expect(() => verifyPhase7BuildContract(frontendDirectory)).toThrow(/asset contract/i);
  });

  test("accepts a contract bound to the exact dist assets", () => {
    const frontendDirectory = frontendFixture();
    const contractPath = writeValidContract(frontendDirectory);
    expect(verifyPhase7BuildContract(frontendDirectory)).toBe(contractPath);
  });

  test.each([
    ["modified JavaScript", (directory) => writeFileSync(
      join(directory, "dist", "assets", "app.js"),
      "console.log('tampered');\n",
    )],
    ["modified index", (directory) => writeFileSync(
      join(directory, "dist", "index.html"),
      "<!doctype html><title>tampered</title>\n",
    )],
    ["deleted asset", (directory) => rmSync(
      join(directory, "dist", "assets", "app.js"),
    )],
    ["added asset", (directory) => writeFileSync(
      join(directory, "dist", "extra.txt"),
      "unexpected\n",
    )],
  ])("rejects a %s after contract creation", (_label, mutate) => {
    const frontendDirectory = frontendFixture();
    writeValidContract(frontendDirectory);
    mutate(frontendDirectory);
    expect(() => verifyPhase7BuildContract(frontendDirectory)).toThrow(/asset contract/i);
  });

  test("rejects a contract copied from a different dist", () => {
    const first = frontendFixture();
    const second = frontendFixture();
    writeFileSync(join(second, "dist", "assets", "app.js"), "different\n");
    const firstContract = writeValidContract(first);
    writeFileSync(
      join(second, "dist", ".elvern-build-contract.json"),
      readFileSync(firstContract),
      { mode: 0o644 },
    );
    expect(() => verifyPhase7BuildContract(second)).toThrow(/asset contract/i);
  });

  test("rejects symlink assets and ignores only the runtime network fixture", () => {
    const frontendDirectory = frontendFixture();
    const fixture = join(frontendDirectory, "dist", "network-guard-fixture");
    mkdirSync(fixture);
    writeFileSync(join(fixture, "service-worker.js"), "fixture\n");
    writeValidContract(frontendDirectory);
    expect(() => verifyPhase7BuildContract(frontendDirectory)).not.toThrow();
    symlinkSync(
      join(frontendDirectory, "dist", "index.html"),
      join(frontendDirectory, "dist", "assets", "linked.js"),
    );
    expect(() => verifyPhase7BuildContract(frontendDirectory)).toThrow(/symlink/i);
  });

  test("runner builds by default while CI may verify an existing build", () => {
    const runner = readFileSync(
      resolve(process.cwd(), "scripts", "run-cross-browser-playwright.mjs"),
      "utf8",
    );
    expect(runner).toContain('rawArguments.includes("--use-existing-build")');
    expect(runner).toContain('resolve(scriptDirectory, "build-phase7-production.mjs")');
    expect(runner).toContain("verifyPhase7BuildContract(frontendDirectory)");
  });
});


describe("browser-level network authority", () => {
  test.each(["http", "https", "ws", "wss"])(
    "blocks external %s without retaining query data",
    (scheme) => {
      const result = classifyProxyTarget(
        `${scheme}://outside.invalid/private?token=hidden`,
        { upgrade: scheme === "ws" },
      );
      expect(result.allowed).toBe(false);
      expect(result.diagnostic).toMatchObject({
        scheme,
        origin: `${scheme}://outside.invalid`,
        pathname_hash: expect.stringMatching(/^[0-9a-f]{12}$/),
      });
      expect(JSON.stringify(result)).not.toContain("token");
    },
  );

  test.each([
    ["http://127.0.0.1:4173", "http://127.0.0.1:4173/library", false],
    ["https://127.0.0.1:4173", "https://127.0.0.1:4173/library", false],
    ["ws://127.0.0.1:4173", "http://127.0.0.1:4173/socket", true],
    ["wss://127.0.0.1:4173", "https://127.0.0.1:4173/socket", true],
    ["http://[::1]:4173", "http://[::1]:4173/library", false],
  ])("allows only registered exact origin %s", (origin, url, upgrade) => {
    expect(classifyProxyTarget(url, {
      allowedOrigins: new Set([origin]),
      upgrade,
    }).allowed).toBe(true);
    expect(classifyProxyTarget(url.replace("4173", "4174"), {
      allowedOrigins: new Set([origin]),
      upgrade,
    }).allowed).toBe(false);
  });

  test("rejects localhost text and unregistered numeric loopback origins", () => {
    expect(classifyProxyTarget("http://localhost:4173/library", {
      allowedOrigins: new Set(["http://localhost:4173"]),
    }).allowed).toBe(false);
    expect(classifyProxyTarget("http://127.0.0.1:4173/library").allowed).toBe(false);
  });

  test("control API requires its token and registers then revokes exact origins", async () => {
    const token = createNetworkGuardControlToken();
    const proxy = await startLoopbackOnlyProxy({
      initialAllowedOrigins: ["http://127.0.0.1:4173"],
      controlToken: token,
    });
    const control = `http://127.0.0.1:${proxy.port}`;
    try {
      expect((await fetch(`${control}/__elvern_network_guard_state`)).status).toBe(403);
      const request = (path, origin) => fetch(`${control}${path}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Elvern-Network-Guard-Token": token,
        },
        body: JSON.stringify({ origin }),
      });
      expect((await request(
        "/__elvern_network_guard_register",
        "http://127.0.0.1:4180",
      )).status).toBe(200);
      expect(proxy.allowedOrigins.has("http://127.0.0.1:4180")).toBe(true);
      expect((await request(
        "/__elvern_network_guard_unregister",
        "http://127.0.0.1:4180",
      )).status).toBe(200);
      expect(proxy.allowedOrigins.has("http://127.0.0.1:4180")).toBe(false);
      expect((await request(
        "/__elvern_network_guard_register",
        "http://localhost:4180",
      )).status).toBe(400);
    } finally {
      await proxy.close();
    }
  });

  test("Service Worker source has no public absolute network target", () => {
    const serviceWorker = readFileSync(
      resolve(process.cwd(), "public", "sw.js"),
      "utf8",
    );
    expect(serviceWorker).not.toMatch(/https?:\/\/(?!elvern\.local)/);
    expect(serviceWorker).not.toMatch(/wss?:\/\//);
  });

  test("cross-browser configuration does not bypass numeric loopback authority", () => {
    const config = readFileSync(
      resolve(process.cwd(), "playwright.cross-browser.config.js"),
      "utf8",
    );
    expect(config).toContain('bypass: "<-loopback>"');
    expect(config).toContain('"network.proxy.allow_hijacking_localhost": true');
  });
});
