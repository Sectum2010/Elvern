import { spawnSync } from "node:child_process";
import { readdirSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import playwrightPackage from "playwright/package.json" with { type: "json" };


const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const frontendDirectory = resolve(scriptDirectory, "..");
const cliPath = resolve(frontendDirectory, "node_modules/playwright/cli.js");
const browserCache = process.env.PLAYWRIGHT_BROWSERS_PATH
  ? resolve(process.env.PLAYWRIGHT_BROWSERS_PATH)
  : resolve(homedir(), ".cache/ms-playwright");
const expectedVersion = "1.60.0";


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


if (playwrightPackage.version !== expectedVersion) {
  console.error(
    `Playwright version mismatch: expected ${expectedVersion}, found ${playwrightPackage.version}. Run npm ci before installing browsers.`,
  );
  process.exit(1);
}

const startedAt = Date.now();
const beforeBytes = directorySize(browserCache);
console.log(`Installing Firefox and WebKit for Playwright ${expectedVersion}.`);
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
