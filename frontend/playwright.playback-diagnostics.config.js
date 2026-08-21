import { defineConfig } from "@playwright/test";


const port = Number(process.env.ELVERN_DIAGNOSTICS_BROWSER_PORT || 4217);
const origin = `http://127.0.0.1:${port}`;


export default defineConfig({
  testDir: "./tests-diagnostics",
  testMatch: "*.pw.js",
  outputDir: "../tmp/playwright-playback-diagnostics-results",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  workers: 1,
  fullyParallel: false,
  reporter: "line",
  webServer: {
    command: `exec ./node_modules/.bin/vite --host 127.0.0.1 --port ${port} --strictPort`,
    url: origin,
    reuseExistingServer: false,
    timeout: 120_000,
  },
  use: {
    baseURL: origin,
    headless: true,
    serviceWorkers: "block",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: { browserName: "chromium" } },
    { name: "firefox", use: { browserName: "firefox" } },
    { name: "webkit", use: { browserName: "webkit" } },
  ],
});
