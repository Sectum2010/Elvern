import { defineConfig } from "@playwright/test";


const port = Number(process.env.ELVERN_PHASE6D_SW_PORT || 4197);
const prefix = "abcd2345";


export default defineConfig({
  testDir: "./tests-phase6d",
  testMatch: "phase6d-service-worker.pw.js",
  outputDir: "../tmp/playwright-phase6d-results",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  webServer: {
    command: `exec env ELVERN_URL_PREFIX=${prefix} ELVERN_FRONTEND_PORT=${port} node server.mjs`,
    url: `http://127.0.0.1:${port}/${prefix}/`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
  use: {
    baseURL: `http://127.0.0.1:${port}/${prefix}/`,
    serviceWorkers: "allow",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium-desktop-production",
      use: { browserName: "chromium", viewport: { width: 1440, height: 900 } },
    },
    {
      name: "chromium-mobile-production",
      use: {
        browserName: "chromium",
        viewport: { width: 390, height: 844 },
        isMobile: true,
        hasTouch: true,
      },
    },
  ],
});
