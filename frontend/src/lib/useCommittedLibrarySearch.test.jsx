import { act, renderHook } from "@testing-library/react";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, test } from "vitest";

import { useCommittedLibrarySearch } from "./useCommittedLibrarySearch.js";


function useHarness() {
  const location = useLocation();
  const navigate = useNavigate();
  const search = useCommittedLibrarySearch({
    committedQuery: new URLSearchParams(location.search).get("q") || "",
    location,
    navigate,
  });
  return { location, search };
}


function wrapper({ children }) {
  return <MemoryRouter initialEntries={["/library?category=anime&q=akira"]}>{children}</MemoryRouter>;
}


describe("committed Library search controller", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  test("draft edits do not change the URL until Enter commits with replace", () => {
    const { result } = renderHook(useHarness, { wrapper });

    act(() => result.current.search.updateDraft("static", "arrival"));
    expect(result.current.location.search).toBe("?category=anime&q=akira");
    expect(result.current.search.committedQuery).toBe("akira");
    expect(result.current.search.staticDraft).toBe("arrival");

    act(() => result.current.search.commit("static"));
    expect(result.current.location.search).toBe("?category=anime&q=arrival");
    expect(result.current.search.staticDraft).toBe("arrival");
    expect(result.current.search.floatingDraft).toBe("arrival");
    expect(result.current.search.activeDraftSource).toBe(null);
  });

  test("active draft source locks the other input until commit, Escape, or Clear", () => {
    const { result } = renderHook(useHarness, { wrapper });

    act(() => result.current.search.updateDraft("floating", "matrix"));
    expect(result.current.search.activeDraftSource).toBe("floating");
    expect(result.current.search.isSourceLocked("static")).toBe(true);

    act(() => result.current.search.revert("floating"));
    expect(result.current.search.activeDraftSource).toBe(null);
    expect(result.current.search.staticDraft).toBe("akira");
    expect(result.current.search.floatingDraft).toBe("akira");

    act(() => result.current.search.updateDraft("static", ""));
    act(() => result.current.search.clear("static"));
    expect(result.current.location.search).toBe("?category=anime");
    expect(result.current.search.floatingExpanded).toBe(false);
  });

  test("non-owner update, commit, clear, and revert calls are safe no-ops", () => {
    const { result } = renderHook(useHarness, { wrapper });

    act(() => result.current.search.updateDraft("static", "arrival"));
    expect(result.current.search.activeDraftSource).toBe("static");

    act(() => result.current.search.updateDraft("floating", "matrix"));
    act(() => result.current.search.commit("floating"));
    act(() => result.current.search.clear("floating"));
    act(() => result.current.search.revert("floating"));

    expect(result.current.location.search).toBe("?category=anime&q=akira");
    expect(result.current.search.staticDraft).toBe("arrival");
    expect(result.current.search.floatingDraft).toBe("akira");
    expect(result.current.search.activeDraftSource).toBe("static");
  });

  test("floating ownership also blocks every static mutation entry point", () => {
    const { result } = renderHook(useHarness, { wrapper });

    act(() => result.current.search.updateDraft("floating", "matrix"));
    act(() => result.current.search.updateDraft("static", "arrival"));
    act(() => result.current.search.commit("static"));
    act(() => result.current.search.clear("static"));
    act(() => result.current.search.revert("static"));

    expect(result.current.location.search).toBe("?category=anime&q=akira");
    expect(result.current.search.staticDraft).toBe("akira");
    expect(result.current.search.floatingDraft).toBe("matrix");
    expect(result.current.search.activeDraftSource).toBe("floating");
  });

  test("floating expansion is stored as one boolean per canonical list path", () => {
    const { result, unmount } = renderHook(useHarness, { wrapper });

    act(() => result.current.search.toggleFloatingExpanded());
    expect(result.current.search.floatingExpanded).toBe(true);
    const storedValues = Object.values(window.sessionStorage);
    expect(storedValues).toEqual(["1"]);
    unmount();

    const next = renderHook(useHarness, { wrapper });
    expect(next.result.current.search.floatingExpanded).toBe(true);
  });
});
