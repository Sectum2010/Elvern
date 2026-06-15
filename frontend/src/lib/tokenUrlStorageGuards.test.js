import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "vitest";
import { fileURLToPath } from "node:url";


const SRC_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SELF = "lib/tokenUrlStorageGuards.test.js";


function listSourceFiles(directory) {
  const entries = fs.readdirSync(directory, { withFileTypes: true });
  return entries.flatMap((entry) => {
    const fullPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      return listSourceFiles(fullPath);
    }
    if (!/\.(js|jsx|mjs)$/.test(entry.name)) {
      return [];
    }
    return [fullPath];
  });
}


function relativeSourcePath(filePath) {
  return path.relative(SRC_DIR, filePath).split(path.sep).join("/");
}


function readFrontendFiles() {
  return listSourceFiles(SRC_DIR)
    .map((filePath) => ({
      filePath,
      relativePath: relativeSourcePath(filePath),
      source: fs.readFileSync(filePath, "utf8"),
    }))
    .filter((entry) => entry.relativePath !== SELF && !/\.test\.(js|jsx|mjs)$/.test(entry.relativePath));
}


test("localStorage never stores token-bearing URL material", () => {
  const sensitiveLocalStoragePattern = /localStorage\s*\.\s*setItem\s*\([^;\n]*(?:token|stream|download|protocol|invite|url)/i;
  for (const entry of readFrontendFiles()) {
    assert.doesNotMatch(
      entry.source,
      sensitiveLocalStoragePattern,
      `${entry.relativePath} must not persist token-bearing URL material in localStorage`,
    );
  }
});


test("sessionStorage token URL storage is limited to the Infuse handoff fallback", () => {
  const urlBearingStoragePattern = /sessionStorage\s*\.\s*setItem\s*\([\s\S]{0,260}(?:stream|download|protocol|url)/gi;
  for (const entry of readFrontendFiles()) {
    const matches = entry.source.matchAll(urlBearingStoragePattern);
    for (const match of matches) {
      const block = match[0];
      assert.ok(
        entry.relativePath === "pages/DetailPage.jsx"
          && block.includes("infuseFallbackStorageKey")
          && entry.source.includes('IOS_INFUSE_HANDOFF_STORAGE_PREFIX = "elvern-ios-handoff"')
          && entry.source.includes("saveInfuseFallbackHandoffState"),
        `${entry.relativePath} has unexpected sessionStorage token URL storage`,
      );
    }
  }
});


test("Infuse fallback storage is explicit, short-lived, and not shared with VLC", () => {
  const source = fs.readFileSync(path.join(SRC_DIR, "pages/DetailPage.jsx"), "utf8");
  assert.match(source, /IOS_INFUSE_HANDOFF_STORAGE_PREFIX\s*=\s*["']elvern-ios-handoff["']/);
  assert.match(source, /IOS_INFUSE_HANDOFF_STORAGE_MAX_AGE_MS\s*=\s*15\s*\*\s*60\s*\*\s*1000/);
  assert.match(source, /function saveInfuseFallbackHandoffState/);
  assert.match(source, /function readInfuseFallbackHandoffState/);
  assert.match(source, /function clearInfuseFallbackHandoffState/);
  assert.doesNotMatch(source, /saveIosExternalAppLaunchState/);
  assert.doesNotMatch(source, /readIosExternalAppLaunchState/);
  assert.doesNotMatch(source, /clearIosExternalAppLaunchState/);
  assert.match(source, /targetApp === "infuse"[\s\S]{0,180}saveInfuseFallbackHandoffState/);
  assert.doesNotMatch(source, /targetApp === "vlc"[\s\S]{0,220}saveInfuseFallbackHandoffState/);
});


test("tokenized URL and token fields are not logged to the browser console", () => {
  const sensitiveConsolePattern = /console\s*\.\s*(?:log|warn|error|debug|info)\s*\([^;\n]*(?:token|access_token|stream_url|download_url|controlled_download_url|protocol_url|session_token|details_url|heartbeat_url|progress_url|event_url|close_url)/i;
  for (const entry of readFrontendFiles()) {
    assert.doesNotMatch(
      entry.source,
      sensitiveConsolePattern,
      `${entry.relativePath} must not console.log tokenized URL or token fields`,
    );
  }
});
