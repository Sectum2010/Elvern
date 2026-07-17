import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import http from "node:http";
import path from "node:path";

import { expect, test } from "@playwright/test";


const PREFIX = "/update234/";
const LEGACY_REVISION = "0".repeat(64);


function listen(server) {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve(server.address()));
  });
}


function close(server) {
  return new Promise((resolve) => server.close(resolve));
}


test("an installed worker replaces stale offline visuals after the shell content changes", async ({ context, page }) => {
  const distDir = path.resolve(process.cwd(), "dist");
  const [currentOffline, currentWorker] = await Promise.all([
    readFile(path.join(distDir, "offline.html"), "utf8"),
    readFile(path.join(distDir, "sw.js"), "utf8"),
  ]);
  const currentRevision = createHash("sha256").update(currentOffline).digest("hex");
  expect(currentWorker).toContain(currentRevision);
  const legacyWorker = currentWorker.replaceAll(currentRevision, LEGACY_REVISION);
  const legacyOffline = "<!doctype html><title>Elvern</title><p>OLD_OFFLINE_SHELL_MARKER</p>";
  const onlineDocument = `<!doctype html><title>Elvern</title><script>navigator.serviceWorker.register("./sw.js", { scope: "./", updateViaCache: "none" });</script>`;
  let serveCurrentRevision = false;

  const server = http.createServer((request, response) => {
    const requestUrl = new URL(request.url || "/", "http://elvern.test");
    response.setHeader("Cache-Control", "no-cache");
    if (requestUrl.pathname === `${PREFIX}sw.js`) {
      response.setHeader("Content-Type", "text/javascript; charset=utf-8");
      response.end(serveCurrentRevision ? currentWorker : legacyWorker);
      return;
    }
    if (requestUrl.pathname === `${PREFIX}offline.html`) {
      response.setHeader("Content-Type", "text/html; charset=utf-8");
      response.end(serveCurrentRevision ? currentOffline : legacyOffline);
      return;
    }
    response.setHeader("Content-Type", "text/html; charset=utf-8");
    response.end(onlineDocument);
  });
  const address = await listen(server);
  const baseUrl = `http://127.0.0.1:${address.port}${PREFIX}`;

  try {
    await page.goto(baseUrl);
    await page.evaluate(async () => navigator.serviceWorker.ready);
    await expect.poll(() => page.evaluate(async (revision) => {
      return (await caches.keys()).some((key) => key.includes(revision));
    }, LEGACY_REVISION)).toBe(true);

    serveCurrentRevision = true;
    await page.evaluate(async () => {
      const registration = await navigator.serviceWorker.getRegistration();
      await registration.update();
    });

    await expect.poll(() => page.evaluate(async (revision) => {
      return (await caches.keys()).some((key) => key.includes(revision));
    }, currentRevision)).toBe(true);
    await expect.poll(() => page.evaluate(async (revision) => {
      return (await caches.keys()).some((key) => key.includes(revision));
    }, LEGACY_REVISION)).toBe(false);

    await context.setOffline(true);
    await page.goto(`${baseUrl}library/42`, { waitUntil: "domcontentloaded" });
    await expect(page.locator("#elvern-connection-shell")).toBeAttached();
    await expect(page.locator("body")).not.toContainText("OLD_OFFLINE_SHELL_MARKER");
  } finally {
    await context.setOffline(false);
    await close(server);
  }
});
