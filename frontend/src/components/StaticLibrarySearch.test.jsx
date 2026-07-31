import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { StaticLibrarySearch } from "./StaticLibrarySearch.jsx";


function searchController() {
  return {
    commit: vi.fn(),
    clear: vi.fn(),
    committedQuery: "",
    isSourceLocked: () => false,
    revert: vi.fn(),
    staticDraft: "",
    updateDraft: vi.fn(),
  };
}


describe("StaticLibrarySearch layout contract", () => {
  test("applies layout class to the form and field class only to the label", () => {
    render(<StaticLibrarySearch
      fieldClassName="field-only"
      formClassName="library-desktop-hero__search"
      search={searchController()}
    />);

    const input = screen.getByRole("searchbox", { name: "Search library" });
    expect(input.closest("form")).toHaveClass("library-desktop-hero__search");
    expect(input.closest("label")).toHaveClass("field-only");
    expect(input.closest("label")).not.toHaveClass("library-desktop-hero__search");
  });

  test("clearing a committed query immediately clears the formal search", () => {
    const search = {
      ...searchController(),
      committedQuery: "matrix",
      staticDraft: "matrix",
    };
    render(<StaticLibrarySearch search={search} />);

    fireEvent.change(screen.getByRole("searchbox", { name: "Search library" }), {
      target: { value: "" },
    });

    expect(search.updateDraft).toHaveBeenCalledWith("static", "");
    expect(search.clear).toHaveBeenCalledWith("static");
  });
});
