#!/usr/bin/env node

import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath, pathToFileURL } from "node:url";
import { loadEnv } from "vite";

import { resolvePublicConnectivityProbeRegistry } from "../src/lib/publicConnectivityProbes.js";
import { buildInlineConnectivityRuntimeSource } from "../src/lib/connectivityRuntimeCore.js";


export const OFFLINE_SHELL_REVISION_PLACEHOLDER = "__ELVERN_OFFLINE_SHELL_REVISION__";
export const PUBLIC_CONNECTIVITY_PROBES_JSON_PLACEHOLDER = "__ELVERN_PUBLIC_CONNECTIVITY_PROBES_JSON__";
export const CONNECTIVITY_RUNTIME_PLACEHOLDER = "/*__ELVERN_CONNECTIVITY_RUNTIME__*/";


function configuredPublicConnectivityProbes() {
  const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const viteEnvironment = loadEnv(
    process.env.NODE_ENV || "production",
    frontendRoot,
    "VITE_ELVERN_PUBLIC_CONNECTIVITY_PROBE_",
  );
  return resolvePublicConnectivityProbeRegistry({
    pluralValue: process.env.VITE_ELVERN_PUBLIC_CONNECTIVITY_PROBE_URLS
      ?? viteEnvironment.VITE_ELVERN_PUBLIC_CONNECTIVITY_PROBE_URLS
      ?? "",
    singularValue: process.env.VITE_ELVERN_PUBLIC_CONNECTIVITY_PROBE_URL
      ?? viteEnvironment.VITE_ELVERN_PUBLIC_CONNECTIVITY_PROBE_URL
      ?? "",
  });
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


export function stampPublicConnectivityProbes(source, probes) {
  const occurrences = source.split(PUBLIC_CONNECTIVITY_PROBES_JSON_PLACEHOLDER).length - 1;
  if (occurrences !== 1) {
    throw new Error(`Expected exactly one public connectivity probe registry placeholder, found ${occurrences}.`);
  }
  const serialized = JSON.stringify((probes || []).map((probe) => ({
    id: probe.id,
    url: probe.url,
    expectedStatuses: [...(probe.expectedStatuses || [])],
  })))
    .replaceAll("<", "\\u003c")
    .replaceAll(">", "\\u003e")
    .replaceAll("\u2028", "\\u2028")
    .replaceAll("\u2029", "\\u2029");
  const escapedJson = JSON.stringify(serialized).slice(1, -1);
  return source.replace(PUBLIC_CONNECTIVITY_PROBES_JSON_PLACEHOLDER, escapedJson);
}


export function stampConnectivityRuntime(source) {
  const occurrences = source.split(CONNECTIVITY_RUNTIME_PLACEHOLDER).length - 1;
  if (occurrences !== 1) {
    throw new Error(`Expected exactly one connectivity runtime placeholder, found ${occurrences}.`);
  }
  return source.replace(CONNECTIVITY_RUNTIME_PLACEHOLDER, buildInlineConnectivityRuntimeSource());
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
  const publicProbes = configuredPublicConnectivityProbes();
  const offlineContent = stampConnectivityRuntime(stampPublicConnectivityProbes(offlineSource, publicProbes));
  const revision = computeOfflineShellRevision(offlineContent);
  await Promise.all([
    writeFile(offlinePath, offlineContent, "utf8"),
    writeFile(indexPath, indexSource, "utf8"),
    writeFile(serviceWorkerPath, stampServiceWorkerSource(serviceWorkerSource, revision), "utf8"),
  ]);
  return revision;
}


if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const revision = await stampBuiltOfflineShell();
  process.stdout.write(`Stamped offline shell revision ${revision.slice(0, 12)}.\n`);
}
