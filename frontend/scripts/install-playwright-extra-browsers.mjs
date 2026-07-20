import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { readdirSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const frontendDirectory = resolve(scriptDirectory, "..");
const cliPath = resolve(frontendDirectory, "node_modules/playwright/cli.js");
const require = createRequire(import.meta.url);
const playwrightPackage = require("playwright/package.json");
const playwrightTestPackage = require("@playwright/test/package.json");
const browserCache = process.env.PLAYWRIGHT_BROWSERS_PATH
  ? resolve(process.env.PLAYWRIGHT_BROWSERS_PATH)
  : resolve(homedir(), ".cache/ms-playwright");
const installedVersion = String(playwrightPackage.version || "");


function directorySize(path) {
  try {
    return readdirSync(path, { withFileTypes: true }).reduce((total, entry) => {
      const childPath = resolve(path, entry.name);
      return total + (entry.isDirectory() ? directorySize(childPath) : statSync(childPath).size);
    }, 0);
  } catch {
    return 0;
  }
}


function formatMiB(bytes) {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}


if (!installedVersion || playwrightTestPackage.version !== installedVersion) {
  console.error(
    `Playwright package mismatch: playwright=${playwrightPackage.version || "missing"}, @playwright/test=${playwrightTestPackage.version || "missing"}. Run npm ci before installing browsers.`,
  );
  process.exit(1);
}

const cliVersionResult = spawnSync(process.execPath, [cliPath, "--version"], {
  cwd: frontendDirectory,
  encoding: "utf-8",
});
const cliVersion = String(cliVersionResult.stdout || "").match(/(\d+\.\d+\.\d+)/)?.[1] || "";
if (cliVersionResult.status !== 0 || cliVersion !== installedVersion) {
  console.error(
    `Playwright CLI mismatch: packages=${installedVersion}, cli=${cliVersion || "unavailable"}. Run npm ci before installing browsers.`,
  );
  process.exit(1);
}

const startedAt = Date.now();
const beforeBytes = directorySize(browserCache);
console.log(`Installing Firefox and WebKit for Playwright ${installedVersion}.`);
console.log(`Browser cache: ${browserCache}`);
const result = spawnSync(process.execPath, [cliPath, "install", "firefox", "webkit"], {
  cwd: frontendDirectory,
  env: process.env,
  stdio: "inherit",
});
const elapsedSeconds = (Date.now() - startedAt) / 1000;
const afterBytes = directorySize(browserCache);

if (result.status !== 0) {
  console.error("Browser installation failed.");
  console.error("Retry with: npm run playwright:install:extra --prefix frontend");
  process.exit(result.status || 1);
}

console.log(`Installed in ${elapsedSeconds.toFixed(1)} seconds.`);
console.log(`Browser cache size: ${formatMiB(afterBytes)} (${formatMiB(afterBytes - beforeBytes)} added).`);
