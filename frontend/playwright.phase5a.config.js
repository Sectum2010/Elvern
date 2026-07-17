import { defineConfig } from "@playwright/test";


const port = Number(process.env.ELVERN_PHASE5A_SW_PORT || 4195);
const prefix = process.env.ELVERN_PHASE5A_SW_PREFIX || "phase5a2";
const origin = `http://127.0.0.1:${port}`;


export default defineConfig({
  testDir: "./tests-production",
  testMatch: "**/*.pw.js",
  outputDir: "../tmp/playwright-phase5a-results",
  timeout: 45_000,
  expect: { timeout: 8_000 },
  webServer: {
    command: `ELVERN_URL_PREFIX=${prefix} ELVERN_FRONTEND_PORT=${port} node server.mjs`,
    url: `${origin}/${prefix}/offline.html`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
  use: {
    baseURL: `${origin}/${prefix}/`,
    serviceWorkers: "allow",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium-production",
      use: { browserName: "chromium", viewport: { width: 1280, height: 800 } },
    },
  ],
});
