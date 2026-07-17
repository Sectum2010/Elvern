import { readFileSync } from "node:fs";

import { describe, expect, test } from "vitest";

import {
  CONNECTION_OOPS_COPY,
  CONNECTION_OOPS_TITLE,
  CONNECTION_STATUS_WORDS,
  STARTUP_HEALTH_PROBE_INTERVAL_MS,
  STARTUP_UNREACHABLE_DELAY_MS,
} from "./startupConnection.js";


const indexHtml = readFileSync(`${process.cwd()}/index.html`, "utf8");
const offlineHtml = readFileSync(`${process.cwd()}/public/offline.html`, "utf8");
const serviceWorker = readFileSync(`${process.cwd()}/public/sw.js`, "utf8");


describe("static connection shell contract", () => {
  test("index and offline shells share the exact product copy and waiting words", () => {
    for (const html of [indexHtml, offlineHtml]) {
      expect(html).toContain('id="elvern-connection-shell"');
      expect(html).toContain(CONNECTION_OOPS_TITLE);
      expect(html).toContain(CONNECTION_OOPS_COPY);
      expect(html).toContain(">Retry<");
      CONNECTION_STATUS_WORDS.forEach((word) => expect(html).toContain(word));
    }
  });

  test("both shells paint the dark background without waiting for bundled CSS", () => {
    for (const html of [indexHtml, offlineHtml]) {
      expect(html).toMatch(/html,\s*body\s*\{[^}]*background:\s*#080b12/s);
      expect(html).toContain('aria-live="polite"');
      expect(html).toContain('aria-hidden="true"');
      expect(html).not.toMatch(/https?:\/\/(?:fonts|cdn)\./i);
    }
  });

  test("familiars are unframed and waiting text moves unless motion is reduced", () => {
    for (const html of [indexHtml, offlineHtml]) {
      expect(html).toMatch(/\.elvern-familiar-stage\s*\{[^}]*(?:width:\s*128px)[^}]*\}/s);
      expect(html).not.toMatch(/\.elvern-familiar-stage\s*\{[^}]*(?:border|background|box-shadow):/s);
      expect(html).toContain("@keyframes elvern-word-bounce");
      expect(html).toMatch(/\.elvern-connection-shell__waiting\s*\{[^}]*animation:\s*elvern-word-bounce/s);
      expect(html).toMatch(/prefers-reduced-motion[\s\S]*\.elvern-connection-shell__waiting\s*\{\s*animation:\s*none\s*!important/s);
    }
  });

  test("offline shell keeps the fixed 60 second and 10 second constants", () => {
    for (const html of [indexHtml, offlineHtml]) {
      expect(html).toContain(`const UNREACHABLE_DELAY_MS = ${STARTUP_UNREACHABLE_DELAY_MS}`);
      expect(html).toContain(`const HEALTH_PROBE_INTERVAL_MS = ${STARTUP_HEALTH_PROBE_INTERVAL_MS}`);
    }
    expect(indexHtml).toContain("__elvernStaticConnectionShellCleanup");
    expect(indexHtml).toContain("__elvernConnectionStartedAt = Date.now()");
  });

  test("service worker allowlists only offline.html and never caches private routes", () => {
    expect(serviceWorker).toContain('"offline.html"');
    expect(serviceWorker).not.toMatch(/cache\.addAll\([^)]*(?:api|library|poster|auth)/is);
    expect(serviceWorker).not.toMatch(/cache\.put\(/);
    expect(serviceWorker).toContain('request.mode !== "navigate"');
    expect(serviceWorker).toContain('const LEGACY_CACHE_FAMILY = "elvern-shell"');
    expect(serviceWorker).toContain("caches.delete(key)");
    expect(serviceWorker).toContain("self.clients.claim()");
  });
});
