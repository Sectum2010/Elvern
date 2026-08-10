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

  test("keeps the approved Meridian light and mixed surface tokens", () => {
    const css = readFileSync(controlCenterCssPath, "utf8");
    for (const token of [
      "--mer-root: #f3e9d8",
      "--mer-sidebar: #fff4e6",
      "--mer-card: #fff4e6",
      "--mer-card-border: rgba(20, 22, 26, 0.08)",
      "--mer-surface: #f1e8d7",
      "--mer-hover: #f6ecdc",
      "--mer-input: #fcf1e2",
      "--mer-input-border: rgba(20, 22, 26, 0.15)",
      "--mer-divider: rgba(20, 22, 26, 0.07)",
    ]) {
      expect(css).toContain(token);
    }
  });

  test("keeps the approved visual geometry fixes scoped to Meridian controls", () => {
    const css = readFileSync(controlCenterCssPath, "utf8");

    expect(css).toContain(".meridian-connection-card .meridian-row-copy { flex: 1; }");
    expect(css).toContain(".meridian-source-list__empty { margin: 0; padding-top: 12px; }");
    expect(css).toContain(".meridian-cloud-libraries-card { min-height: 158px; }");
    expect(css).toContain(".meridian-hidden-scope { align-self: flex-start; }");
    expect(css).toContain(".meridian-age-refresh:hover { color: #fff; background: linear-gradient(135deg, #3d5ef5, #2440c9);");
    expect(css).toContain(".meridian-security-gauge__dial-wrap {");
    expect(css).not.toContain(".meridian-security-gauge__score {\n  top:");
    expect(css).not.toContain(".meridian-security-gauge__mode {\n  top:");
    expect(css).toContain(".meridian-create-user-card {\n  box-sizing: border-box;");
    expect(css).toContain("min-height: 144px;\n  height: auto;");
    expect(css).not.toContain("\n  height: 144px;");
    expect(css).toContain(".meridian-invite-card .admin-invite-code-header > .primary-button {");
    expect(css).toContain(".meridian-invite-card .admin-invite-code-list > .page-subnote:only-child {");
    expect(css).toContain(".meridian-invite-card .admin-invite-code-list:has(> .page-subnote:only-child) {");
  });
});
