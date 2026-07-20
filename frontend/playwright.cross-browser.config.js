import { defineConfig } from "@playwright/test";


const port = Number(process.env.ELVERN_PHASE7_BROWSER_PORT || 4199);
const prefix = process.env.ELVERN_PHASE7_BROWSER_PREFIX || "phase7brwser";
const origin = `http://127.0.0.1:${port}`;


export default defineConfig({
  testDir: "./tests-phase7",
  testMatch: "phase7-cross-browser.pw.js",
  outputDir: "../tmp/playwright-phase7-cross-browser-results",
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
    serviceWorkers: "allow",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "firefox-desktop-production",
      use: { browserName: "firefox", viewport: { width: 1440, height: 900 } },
    },
    {
      name: "webkit-desktop-production",
      use: { browserName: "webkit", viewport: { width: 1440, height: 900 } },
    },
  ],
});
