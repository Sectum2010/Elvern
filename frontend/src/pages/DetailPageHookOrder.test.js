import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, test } from "vitest";

const detailPagePath = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "DetailPage.jsx",
);

function readDetailPage() {
  return fs.readFileSync(detailPagePath, "utf8");
}

describe("DetailPage hook-order guards", () => {
  test("does not declare React hooks after early render returns", () => {
    const source = readDetailPage();
    const firstEarlyReturnIndex = source.indexOf("if (loading) {");
    expect(firstEarlyReturnIndex).toBeGreaterThan(0);

    const afterEarlyReturns = source.slice(firstEarlyReturnIndex);
    expect(afterEarlyReturns).not.toMatch(/\buse(?:Effect|Memo|Ref|State|Callback|Reducer|Context|LayoutEffect)\s*\(/);
  });

  test("fullscreen touch pan lock does not block track menu scrolling", () => {
    const source = readDetailPage();
    const handlerStart = source.indexOf("const preventViewportPan = (event) => {");
    expect(handlerStart).toBeGreaterThan(0);
    const handlerEnd = source.indexOf("shell.addEventListener(\"touchmove\", preventViewportPan", handlerStart);
    expect(handlerEnd).toBeGreaterThan(handlerStart);
    const handlerSource = source.slice(handlerStart, handlerEnd);

    expect(handlerSource).toContain(".elvern-overlay__track-menu");
    expect(handlerSource).toContain(".elvern-overlay__menu-host");
    expect(handlerSource).toContain(".elvern-overlay__track-menu-item");
    expect(handlerSource.indexOf("target?.closest?.(\".elvern-overlay__track-menu\")"))
      .toBeLessThan(handlerSource.indexOf("event.preventDefault()"));
  });
});
