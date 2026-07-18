import { readFileSync } from "node:fs";

import { describe, expect, test } from "vitest";

import {
  CONNECTION_OFFLINE_OOPS_COPY,
  CONNECTION_GENERIC_OOPS_COPY,
  CONNECTION_OOPS_TITLE,
  CONNECTION_SERVER_OOPS_COPY,
  CONNECTION_VPN_OOPS_COPY,
  CONNECTION_FAMILIARS,
  CONNECTION_STATUS_WORDS,
  STARTUP_HEALTH_PROBE_INTERVAL_MS,
  STARTUP_SHELL_REVEAL_DELAY_MS,
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
      expect(html).toContain(CONNECTION_SERVER_OOPS_COPY);
      expect(html).toContain(CONNECTION_VPN_OOPS_COPY);
      expect(html).toContain(CONNECTION_OFFLINE_OOPS_COPY);
      expect(html).toContain(CONNECTION_GENERIC_OOPS_COPY);
      expect(html).toContain('navigator.onLine === false ? "internet_offline"');
      expect(html).toContain('/_elvern/frontend-health');
      expect(html).toContain("__ELVERN_PUBLIC_CONNECTIVITY_PROBES_JSON__");
      expect(html).toContain(">Retry<");
      CONNECTION_STATUS_WORDS.forEach((word) => expect(html).toContain(word));
    }
  });

  test("all shells use the new exact server copy and no old server copy remains", () => {
    for (const html of [indexHtml, offlineHtml]) {
      expect(html).toContain("Seems like the server has been bamboozled, we will fix it as soon as possible.");
      expect(html).not.toContain("Seems like the server has been bamboozled, please try again later.");
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

  test("familiars are unframed and waiting letters move individually unless motion is reduced", () => {
    for (const html of [indexHtml, offlineHtml]) {
      expect(html).toMatch(/\.elvern-familiar-stage\s*\{[^}]*(?:width:\s*128px)[^}]*\}/s);
      expect(html).not.toMatch(/\.elvern-familiar-stage\s*\{[^}]*(?:border|background|box-shadow):/s);
      expect(html).not.toContain("#elvern-connection-shell::before");
      expect(html).not.toContain("#elvern-connection-shell::after");
      expect(html).toContain("@keyframes elvern-letter-wave");
      expect(html).toMatch(/\.elvern-connection-shell__letter\s*\{[^}]*animation:\s*elvern-letter-wave/s);
      expect(html).not.toContain("@keyframes elvern-word-bounce");
      expect(html).toMatch(/prefers-reduced-motion[\s\S]*\.elvern-connection-shell__letter\s*\{\s*animation:\s*none\s*!important/s);
      CONNECTION_FAMILIARS.forEach((familiar) => expect(html).toContain(`elvern-familiar--${familiar}`));
    }
  });

  test("the loading typeface stays scoped to the shell and includes Oops and Retry", () => {
    expect(indexHtml).not.toMatch(/body\s*\{[^}]*font-family:\s*ui-monospace/s);
    for (const html of [indexHtml, offlineHtml]) {
      expect(html).toMatch(/#elvern-connection-shell\s*\{[^}]*font-family:\s*ui-monospace/s);
      expect(html).toMatch(/\.elvern-connection-shell__oops p\s*\{[^}]*font-family:\s*inherit/s);
      expect(html).toMatch(/\.elvern-connection-shell__retry\s*\{[^}]*font-family:\s*inherit/s);
    }
  });

  test("normal startup waits 400ms before revealing the shell and Retry restarts connecting", () => {
    expect(indexHtml).toContain(`const SHELL_REVEAL_DELAY_MS = ${STARTUP_SHELL_REVEAL_DELAY_MS}`);
    expect(indexHtml).toContain("elvern-connection-shell--visible");
    for (const html of [indexHtml, offlineHtml]) {
      expect(html).toContain("function restartConnectingCycle()");
      expect(html).toContain('shell.dataset.state = "connecting"');
      expect(html).toContain("setWaitingWord(WORDS[0])");
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

  test("static bootstrap never reloads and offline recovery is bounded by confirmation and cooldown", () => {
    expect(indexHtml).toContain("__elvernAppBootstrapStarted");
    expect(indexHtml).toContain("__elvernRuntimeReady");
    expect(indexHtml).not.toContain("window.location.reload()");
    expect(offlineHtml).toContain("consecutiveServiceSuccesses");
    expect(offlineHtml).toContain("RECOVERY_RELOAD_COOLDOWN_MS = 30000");
    expect(offlineHtml).toContain("consecutiveServiceSuccesses < 2");
    expect(offlineHtml).toContain("reload blocked because cooldown storage is unavailable");
    expect(offlineHtml).toContain("window.location.reload()");
  });

  test("static probes use status-only ordered requests and never consume response bodies", () => {
    for (const html of [indexHtml, offlineHtml]) {
      expect(html).toContain('credentials: "omit"');
      expect(html).toContain('referrerPolicy: "no-referrer"');
      expect(html).toContain('cache: "no-store"');
      expect(html).not.toContain("response.text(");
      expect(html).not.toContain("response.json(");
      expect(html).not.toContain("response.clone(");
    }
  });

  test("service worker allowlists only offline.html and never caches private routes", () => {
    expect(serviceWorker).toContain('"offline.html"');
    expect(serviceWorker).toContain('const OFFLINE_SHELL_REVISION = "__ELVERN_OFFLINE_SHELL_REVISION__"');
    expect(serviceWorker).toContain("${OFFLINE_CACHE_FAMILY}${OFFLINE_SHELL_REVISION}");
    expect(serviceWorker).not.toMatch(/cache\.addAll\([^)]*(?:api|library|poster|auth)/is);
    expect(serviceWorker).not.toMatch(/cache\.put\(/);
    expect(serviceWorker).toContain('request.mode !== "navigate"');
    expect(serviceWorker).toContain('const LEGACY_CACHE_FAMILY = "elvern-shell"');
    expect(serviceWorker).toContain("caches.delete(key)");
    expect(serviceWorker).toContain("self.clients.claim()");
  });
});
