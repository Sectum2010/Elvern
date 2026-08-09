import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, test } from "vitest";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const sourceRoot = resolve(frontendRoot, "src");
const controlCenterCssPath = resolve(sourceRoot, "controlCenter.css");
const textExtensions = new Set([".js", ".jsx", ".css", ".html"]);

function productionSource() {
  const files = readdirSync(sourceRoot, { recursive: true, withFileTypes: true })
    .filter((entry) => entry.isFile() && !entry.name.includes(".test."))
    .map((entry) => resolve(entry.parentPath, entry.name))
    .filter((path) => textExtensions.has(path.slice(path.lastIndexOf("."))));
  return files.map((path) => readFileSync(path, "utf8")).join("\n");
}

describe("Control Center production guards", () => {
  test("does not ship known private-demo identities or hosts", () => {
    const source = productionSource();
    expect(source).not.toContain("Qi Yang");
    expect(source).not.toContain("spark-e245");
    expect(source).not.toMatch(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i);
    expect(source).not.toMatch(/\b\d{6,}-[a-z0-9_-]+\.apps\.googleusercontent\.com\b/i);
    expect(source).not.toMatch(/["'`]\/[a-hjkmnp-z2-9]{8,24}\/["'`]/i);
    expect(source).not.toMatch(/["'`]\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z["'`]/);
    expect(source).not.toMatch(/\/home\/[a-z0-9._-]+\//i);
    expect(source).not.toContain("support.js");
  });

  test("uses local Control Center fonts with bundled licenses and no Google runtime", () => {
    const source = productionSource();
    expect(source).not.toContain("fonts.googleapis.com");
    expect(source).not.toContain("fonts.gstatic.com");
    for (const filename of [
      "archivo-latin-variable.woff2",
      "sora-latin-variable.woff2",
      "space-grotesk-latin-variable.woff2",
      "Archivo-OFL.txt",
      "Sora-OFL.txt",
      "SpaceGrotesk-OFL.txt",
    ]) {
      expect(existsSync(resolve(sourceRoot, "assets/fonts/control-center", filename))).toBe(true);
    }
  });

  test("keeps every Control Center class selector inside the desktop namespace", () => {
    const css = readFileSync(controlCenterCssPath, "utf8");
    const unscopedSelectors = css
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => /^\.control-center(?!-desktop)/.test(line));

    expect(unscopedSelectors).toEqual([]);
  });

  test("does not restyle retained legacy flows through broad desktop selectors", () => {
    const css = readFileSync(controlCenterCssPath, "utf8");
    for (const forbiddenSelector of [
      ".control-center-desktop .settings-card",
      ".control-center-desktop .admin-card",
      ".control-center-desktop .ghost-button",
      ".control-center-desktop .primary-button",
      ".control-center-desktop input",
      ".meridian-control-center .settings-card",
      ".meridian-control-center .admin-card",
      ".meridian-control-center .ghost-button",
      ".meridian-control-center input",
    ]) {
      expect(css).not.toContain(forbiddenSelector);
    }
  });
});
