import net from "node:net";

import { describe, expect, test } from "vitest";

import {
  createCrossBrowserPrefix,
  releaseReservedPort,
  reserveAvailablePort,
} from "../../scripts/cross-browser-runner-core.mjs";


describe("cross-browser Playwright runner allocation", () => {
  test("generates unique base32-safe prefixes", () => {
    const prefixes = new Set(Array.from({ length: 20 }, () => createCrossBrowserPrefix()));
    expect(prefixes.size).toBe(20);
    prefixes.forEach((prefix) => expect(prefix).toMatch(/^[a-hjkmnp-z2-9]{8,24}$/));
  });

  test("selects unique free ports even when the old fixed port is occupied", async () => {
    const occupied = net.createServer();
    let testOwnsOccupiedServer = false;
    await new Promise((resolve, reject) => {
      occupied.once("error", (error) => {
        if (error?.code === "EADDRINUSE") {
          resolve();
          return;
        }
        reject(error);
      });
      occupied.listen(4199, "127.0.0.1", () => {
        testOwnsOccupiedServer = true;
        resolve();
      });
    });
    let ports = [];
    try {
      ports = await Promise.all([
        reserveAvailablePort(),
        reserveAvailablePort(),
        reserveAvailablePort(),
      ]);
      expect(new Set(ports).size).toBe(ports.length);
      expect(ports).not.toContain(4199);
    } finally {
      ports.forEach(releaseReservedPort);
      if (testOwnsOccupiedServer) {
        await new Promise((resolve) => occupied.close(resolve));
      }
    }
  });
});
