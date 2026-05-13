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
});
