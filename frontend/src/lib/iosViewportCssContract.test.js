import { readFileSync } from "node:fs";

import { describe, expect, test } from "vitest";


const styles = readFileSync(`${process.cwd()}/src/styles.css`, "utf8");
const indexHtml = readFileSync(`${process.cwd()}/index.html`, "utf8");


describe("iOS auth viewport CSS contract", () => {
  test("all auth inputs use at least 16px and keyboard state top-aligns scrollable auth pages", () => {
    expect(styles).toMatch(/data-elvern-ios-viewport[\s\S]*\.login-form input[\s\S]*font-size:\s*16px/);
    expect(styles).toMatch(/data-elvern-keyboard-open[\s\S]*\.login-screen[\s\S]*align-items:\s*start/);
    expect(styles).toMatch(/data-elvern-keyboard-open[\s\S]*\.login-screen[\s\S]*overflow-y:\s*auto/);
    expect(styles).toMatch(/data-elvern-keyboard-open[\s\S]*\.auth-viewport-page[\s\S]*display:\s*grid/);
  });

  test("a route-external paint floor protects short Login and Detail backgrounds", () => {
    expect(indexHtml).toMatch(/id="elvern-app-paint-floor"[\s\S]*id="root"/);
    expect(styles).toContain("--app-paint-viewport-height");
    expect(styles).toMatch(/#elvern-app-paint-floor\s*\{[^}]*position:\s*fixed[^}]*pointer-events:\s*none/s);
    expect(styles).toMatch(/#root\s*\{[^}]*z-index:\s*1/s);
    expect(styles).toMatch(/@supports\s*\(height:\s*100lvh\)[\s\S]*min-block-size/s);
  });
});
