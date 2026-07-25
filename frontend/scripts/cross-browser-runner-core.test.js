import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { join, resolve } from "node:path";

import { afterEach, describe, expect, test } from "vitest";

import {
  classifyProxyTarget,
  PHASE7_BUILD_CONTRACT,
  verifyPhase7BuildContract,
} from "./cross-browser-runner-core.mjs";


const createdDirectories = [];


function frontendFixture() {
  const root = resolve(process.cwd(), "..", "tmp");
  mkdirSync(root, { recursive: true });
  const directory = mkdtempSync(join(root, "phase7-build-contract-"));
  createdDirectories.push(directory);
  mkdirSync(join(directory, "dist"));
  return directory;
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
    );
    expect(() => verifyPhase7BuildContract(frontendDirectory)).toThrow(/required phase7/i);
  });

  test("accepts only the exact fixed production contract", () => {
    const frontendDirectory = frontendFixture();
    const contractPath = join(
      frontendDirectory,
      "dist",
      ".elvern-build-contract.json",
    );
    writeFileSync(contractPath, JSON.stringify(PHASE7_BUILD_CONTRACT));
    expect(verifyPhase7BuildContract(frontendDirectory)).toBe(contractPath);
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
    "http://localhost:4173/library",
    "http://127.0.0.1:4173/library",
    "http://[::1]:4173/library",
  ])("allows loopback target %s", (url) => {
    expect(classifyProxyTarget(url).allowed).toBe(true);
  });

  test("Service Worker source has no public absolute network target", () => {
    const serviceWorker = readFileSync(
      resolve(process.cwd(), "public", "sw.js"),
      "utf8",
    );
    expect(serviceWorker).not.toMatch(/https?:\/\/(?!elvern\.local)/);
    expect(serviceWorker).not.toMatch(/wss?:\/\//);
  });
});
