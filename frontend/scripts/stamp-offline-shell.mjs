#!/usr/bin/env node

import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath, pathToFileURL } from "node:url";
import { loadEnv } from "vite";


export const OFFLINE_SHELL_REVISION_PLACEHOLDER = "__ELVERN_OFFLINE_SHELL_REVISION__";
export const PUBLIC_CONNECTIVITY_PROBE_URL_PLACEHOLDER = "__ELVERN_PUBLIC_CONNECTIVITY_PROBE_URL__";


function configuredPublicConnectivityProbeUrl() {
  const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const viteEnvironment = loadEnv(
    process.env.NODE_ENV || "production",
    frontendRoot,
    "VITE_ELVERN_PUBLIC_CONNECTIVITY_PROBE_URL",
  );
  return process.env.VITE_ELVERN_PUBLIC_CONNECTIVITY_PROBE_URL
    || viteEnvironment.VITE_ELVERN_PUBLIC_CONNECTIVITY_PROBE_URL
    || "";
}


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


export function stampPublicConnectivityProbeUrl(source, url) {
  const occurrences = source.split(PUBLIC_CONNECTIVITY_PROBE_URL_PLACEHOLDER).length - 1;
  if (occurrences !== 1) {
    throw new Error(`Expected exactly one public connectivity probe placeholder, found ${occurrences}.`);
  }
  const escapedUrl = JSON.stringify(String(url || "").trim()).slice(1, -1);
  return source.replace(PUBLIC_CONNECTIVITY_PROBE_URL_PLACEHOLDER, escapedUrl);
}


export async function stampBuiltOfflineShell({ distDir } = {}) {
  const resolvedDistDir = path.resolve(distDir || path.join(path.dirname(fileURLToPath(import.meta.url)), "../dist"));
  const offlinePath = path.join(resolvedDistDir, "offline.html");
  const indexPath = path.join(resolvedDistDir, "index.html");
  const serviceWorkerPath = path.join(resolvedDistDir, "sw.js");
  const [offlineSource, indexSource, serviceWorkerSource] = await Promise.all([
    readFile(offlinePath, "utf8"),
    readFile(indexPath, "utf8"),
    readFile(serviceWorkerPath, "utf8"),
  ]);
  const publicProbeUrl = configuredPublicConnectivityProbeUrl();
  const offlineContent = stampPublicConnectivityProbeUrl(offlineSource, publicProbeUrl);
  const indexContent = stampPublicConnectivityProbeUrl(indexSource, publicProbeUrl);
  const revision = computeOfflineShellRevision(offlineContent);
  await Promise.all([
    writeFile(offlinePath, offlineContent, "utf8"),
    writeFile(indexPath, indexContent, "utf8"),
    writeFile(serviceWorkerPath, stampServiceWorkerSource(serviceWorkerSource, revision), "utf8"),
  ]);
  return revision;
}


if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const revision = await stampBuiltOfflineShell();
  process.stdout.write(`Stamped offline shell revision ${revision.slice(0, 12)}.\n`);
}
