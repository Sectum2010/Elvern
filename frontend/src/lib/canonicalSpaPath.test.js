import { describe, expect, test, vi } from "vitest";

import {
  applyInitialSpaCanonicalization,
  canonicalizeBrowserLocation,
  canonicalizeSpaPathname,
  classifyLibrarySpaPath,
} from "./canonicalSpaPath.js";


describe("canonical SPA paths", () => {
  test.each([
    ["/library/", "/library"],
    ["/library/local/", "/library/local"],
    ["/library/cloud/", "/library/cloud"],
    ["/library/42/", "/library/42"],
    ["/settings/", "/settings"],
    ["/install/", "/install"],
    ["/desktop/", "/desktop"],
    ["/admin/", "/admin"],
    ["/assistant/", "/assistant"],
    ["/", "/"],
  ])("canonicalizes %s without changing the application root", (input, expected) => {
    expect(canonicalizeSpaPathname(input)).toBe(expected);
  });

  test("preserves a dynamic prefix root and canonicalizes routes beneath it", () => {
    expect(canonicalizeSpaPathname("/abc23456/", { basename: "/abc23456" })).toBe("/abc23456/");
    expect(canonicalizeSpaPathname("/abc23456/library/", { basename: "/abc23456" })).toBe(
      "/abc23456/library",
    );
  });

  test("preserves search and hash", () => {
    expect(canonicalizeBrowserLocation({
      pathname: "/abc23456/library/",
      search: "?category=anime&q=test",
      hash: "#section",
    }, { basename: "/abc23456" })).toEqual({
      pathname: "/abc23456/library",
      search: "?category=anime&q=test",
      hash: "#section",
      href: "/abc23456/library?category=anime&q=test#section",
      changed: true,
    });
  });

  test("does not canonicalize APIs or static resources", () => {
    expect(canonicalizeSpaPathname("/api/library/")).toBe("/api/library/");
    expect(canonicalizeSpaPathname("/health/")).toBe("/health/");
    expect(canonicalizeSpaPathname("/sw.js/")).toBe("/sw.js/");
    expect(canonicalizeSpaPathname("/assets/app.js/")).toBe("/assets/app.js/");
  });

  test("initial canonicalization replaces in place and preserves history state", () => {
    const replaceState = vi.fn();
    const browserWindow = {
      location: {
        pathname: "/abc23456/library/",
        search: "?category=anime",
        hash: "#rail",
      },
      history: {
        state: { restoreLibraryReturn: true },
        replaceState,
      },
    };

    expect(applyInitialSpaCanonicalization(browserWindow, { basename: "/abc23456" })).toBe(true);
    expect(replaceState).toHaveBeenCalledOnce();
    expect(replaceState).toHaveBeenCalledWith(
      { restoreLibraryReturn: true },
      "",
      "/abc23456/library?category=anime#rail",
    );
  });

  test.each([
    ["/library/", "root"],
    ["/library/local/", "source"],
    ["/library/cloud", "source"],
    ["/library/42/", "detail"],
    ["/settings", "other"],
  ])("classifies %s as %s", (pathname, kind) => {
    expect(classifyLibrarySpaPath(pathname).kind).toBe(kind);
  });

  test("classifies complete Library paths beneath a dynamic prefix", () => {
    expect(classifyLibrarySpaPath(
      "/abc23456/library/local/",
      { basename: "/abc23456" },
    )).toEqual({
      kind: "source",
      pathname: "/abc23456/library/local",
    });
  });
});
