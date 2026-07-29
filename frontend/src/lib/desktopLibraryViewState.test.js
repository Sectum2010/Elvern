import { describe, expect, test } from "vitest";

import {
  DEFAULT_LIBRARY_ARRANGE,
  applyLibraryArrangeParams,
  buildLegacySourceRedirectLocation,
  buildLibraryRequestPath,
  buildLibraryViewSearch,
  countLibraryArrangeFilters,
  libraryArrangeEquals,
  normalizeLibraryArrange,
  resolveLibraryArrangeFromSearch,
  toggleLibrarySort,
} from "./desktopLibraryViewState.js";


describe("desktop Library view state", () => {
  test("normalizes repeated genres and qualities into stable canonical order", () => {
    expect(resolveLibraryArrangeFromSearch(
      "?genre=drama&genre=Action&genre=DRAMA&quality=wood&quality=diamond&quality=invalid",
      ["Action", "Drama"],
    )).toEqual({
      source: "all",
      genres: ["Action", "Drama"],
      qualities: ["diamond", "wood"],
      sort: "smart",
    });
  });

  test("writes one repeated parameter per selected value in canonical order", () => {
    const params = applyLibraryArrangeParams(new URLSearchParams("quality=invalid&source=all"), {
      source: "cloud",
      genres: ["Drama", "action", "Drama"],
      qualities: ["gold", "diamond", "wood", "invalid"],
      sort: "za",
    });

    expect(params.toString()).toBe(
      "source=cloud&genre=action&genre=Drama&quality=diamond&quality=gold&quality=wood&sort=za",
    );
  });

  test("builds one request with category, AND groups, repeated OR values, and q", () => {
    expect(buildLibraryRequestPath({
      category: "anime",
      query: " Akira ",
      arrange: {
        source: "cloud",
        genres: ["Drama", "Action"],
        qualities: ["gold", "diamond"],
        sort: "year_desc",
      },
    })).toBe(
      "/api/library/search?category=anime&source=cloud&genre=Action&genre=Drama"
      + "&quality=diamond&quality=gold&sort=year_desc&q=Akira",
    );
  });

  test("preserves unrelated URL state while removing default filters and empty q", () => {
    expect(buildLibraryViewSearch({
      currentSearch: "?category=tv&source=cloud&genre=Drama&quality=gold&q=old&keep=1",
      category: "movies",
      arrange: DEFAULT_LIBRARY_ARRANGE,
      query: "",
    })).toBe("?category=movies&keep=1");
  });

  test("counts each selected filter and counts sort direction only once", () => {
    const arrange = {
      source: "local",
      genres: ["Action", "Drama"],
      qualities: ["diamond", "gold"],
      sort: "za",
    };
    expect(countLibraryArrangeFilters(arrange)).toBe(6);
    expect(libraryArrangeEquals(arrange, {
      source: "LOCAL",
      genres: ["drama", "action"],
      qualities: ["gold", "diamond"],
      sort: "za",
    })).toBe(true);
  });

  test("flips each directional sort and keeps Smart without an alternate", () => {
    const alphabetical = { key: "az", alternateKey: "za" };
    expect(toggleLibrarySort("smart", alphabetical)).toBe("az");
    expect(toggleLibrarySort("az", alphabetical)).toBe("za");
    expect(toggleLibrarySort("za", alphabetical)).toBe("az");
    expect(toggleLibrarySort("year_desc", { key: "smart" })).toBe("smart");
  });

  test("legacy source redirects preserve URL state and let the route source win", () => {
    expect(buildLegacySourceRedirectLocation({
      search: "?source=local&genre=Drama&genre=Action&q=matrix",
      hash: "#target",
    }, "cloud")).toEqual({
      pathname: "/library",
      search: "?source=cloud&genre=Drama&genre=Action&q=matrix",
      hash: "#target",
    });
  });

  test("normalization safely defaults invalid single-value controls", () => {
    expect(normalizeLibraryArrange({
      source: "tape",
      genres: [],
      qualities: ["platinum"],
      sort: "runtime",
    })).toEqual(DEFAULT_LIBRARY_ARRANGE);
  });
});
