import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, test } from "vitest";

const stylesPath = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../styles.css",
);

function readStyles() {
  return fs.readFileSync(stylesPath, "utf8");
}

function cssBlockContaining(styles, selector) {
  const index = styles.indexOf(selector);
  expect(index, `missing CSS selector: ${selector}`).toBeGreaterThanOrEqual(0);
  const start = styles.indexOf("{", index);
  const end = styles.indexOf("}", start);
  expect(start, `missing CSS block start after: ${selector}`).toBeGreaterThanOrEqual(0);
  expect(end, `missing CSS block end after: ${selector}`).toBeGreaterThan(start);
  return styles.slice(start + 1, end);
}

describe("MediaCard CSS hover guards", () => {
  test("library media cards use a full-card hover border without shadow", () => {
    const styles = readStyles();
    const block = cssBlockContaining(styles, ".app-shell--library-root .media-card:hover");

    expect(block).toContain("border-width: 2px");
    expect(block).toContain("border-style: solid");
    expect(block).toContain("border-color: rgba(146, 225, 255, 0.96)");
    expect(block).toContain("box-shadow: none");
  });

  test("modern poster hover does not create a poster-only ring or dark hover shadow", () => {
    const styles = readStyles();
    const block = cssBlockContaining(
      styles,
      ".app-shell--poster-card-modern.app-shell--library-root .media-card:hover .media-card__poster",
    );

    expect(block).toContain("box-shadow: none");
    expect(block).not.toContain("0 0 0");
    expect(block).not.toContain("0 20px 38px");
  });

  test("series rail hover gutter leaves room for the card border at viewport edges", () => {
    const styles = readStyles();
    const hoverStart = styles.indexOf("@media (hover: hover) and (pointer: fine)");
    expect(hoverStart).toBeGreaterThanOrEqual(0);
    const block = cssBlockContaining(styles.slice(hoverStart), ".series-rail__viewport");

    expect(block).toContain("margin-inline: -0.45rem");
    expect(block).toContain("padding-inline: 0.45rem");
  });
});
