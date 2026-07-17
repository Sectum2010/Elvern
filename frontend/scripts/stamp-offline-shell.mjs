#!/usr/bin/env node

import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath, pathToFileURL } from "node:url";


export const OFFLINE_SHELL_REVISION_PLACEHOLDER = "__ELVERN_OFFLINE_SHELL_REVISION__";


export function computeOfflineShellRevision(content) {
  return createHash("sha256").update(content).digest("hex");
}


export function stampServiceWorkerSource(source, revision) {
  const occurrences = source.split(OFFLINE_SHELL_REVISION_PLACEHOLDER).length - 1;
  if (occurrences !== 1) {
    throw new Error(`Expected exactly one offline shell revision placeholder, found ${occurrences}.`);
  }
  if (!/^[a-f0-9]{64}$/.test(revision)) {
    throw new Error("Offline shell revision must be a SHA-256 hex digest.");
  }
  return source.replace(OFFLINE_SHELL_REVISION_PLACEHOLDER, revision);
}


export async function stampBuiltOfflineShell({ distDir } = {}) {
  const resolvedDistDir = path.resolve(distDir || path.join(path.dirname(fileURLToPath(import.meta.url)), "../dist"));
  const offlinePath = path.join(resolvedDistDir, "offline.html");
  const serviceWorkerPath = path.join(resolvedDistDir, "sw.js");
  const [offlineContent, serviceWorkerSource] = await Promise.all([
    readFile(offlinePath),
    readFile(serviceWorkerPath, "utf8"),
  ]);
  const revision = computeOfflineShellRevision(offlineContent);
  await writeFile(serviceWorkerPath, stampServiceWorkerSource(serviceWorkerSource, revision), "utf8");
  return revision;
}


if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const revision = await stampBuiltOfflineShell();
  process.stdout.write(`Stamped offline shell revision ${revision.slice(0, 12)}.\n`);
}
