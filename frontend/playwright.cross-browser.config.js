import { defineConfig } from "@playwright/test";


const port = Number(process.env.ELVERN_PHASE7_BROWSER_PORT || 4199);
const prefix = process.env.ELVERN_PHASE7_BROWSER_PREFIX || "phase7brwser";
const origin = `http://127.0.0.1:${port}`;
const networkProxy = process.env.ELVERN_PHASE7_NETWORK_PROXY;
const guardedProxy = networkProxy
  ? { server: networkProxy, bypass: "<-loopback>" }
  : undefined;


export default defineConfig({
  testDir: "./tests-phase7",
  outputDir: process.env.ELVERN_PHASE7_BROWSER_OUTPUT_DIR || "../tmp/playwright-phase7-cross-browser-results",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  workers: 1,
  webServer: {
    command: `exec env ELVERN_URL_PREFIX=${prefix} ELVERN_FRONTEND_PORT=${port} node server.mjs`,
    url: `${origin}/${prefix}/`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
  use: {
    baseURL: `${origin}/${prefix}/`,
    serviceWorkers: "block",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium-desktop-production",
      testMatch: "phase7-cross-browser.pw.js",
      use: {
        browserName: "chromium",
        viewport: { width: 1440, height: 900 },
        ...(guardedProxy ? { proxy: guardedProxy } : {}),
      },
    },
    {
      name: "firefox-desktop-production",
      testMatch: "phase7-cross-browser.pw.js",
      use: {
        browserName: "firefox",
        viewport: { width: 1440, height: 900 },
        ...(guardedProxy ? { proxy: guardedProxy } : {}),
        launchOptions: {
          firefoxUserPrefs: {
            "network.proxy.allow_hijacking_localhost": true,
          },
        },
      },
    },
    {
      name: "webkit-desktop-production",
      testMatch: "phase7-cross-browser.pw.js",
      use: {
        browserName: "webkit",
        viewport: { width: 1440, height: 900 },
        ...(guardedProxy ? { proxy: guardedProxy } : {}),
      },
    },
    {
      name: "chromium-service-worker-network-guard",
      testMatch: "service-worker-network-guard.pw.js",
      use: {
        browserName: "chromium",
        serviceWorkers: "allow",
        ...(guardedProxy ? { proxy: guardedProxy } : {}),
      },
    },
    {
      name: "firefox-service-worker-network-guard",
      testMatch: "service-worker-network-guard.pw.js",
      use: {
        browserName: "firefox",
        serviceWorkers: "allow",
        ...(guardedProxy ? { proxy: guardedProxy } : {}),
        launchOptions: {
          firefoxUserPrefs: {
            "network.proxy.allow_hijacking_localhost": true,
          },
        },
      },
    },
  ],
});
