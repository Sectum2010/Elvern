import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { FloatingLibrarySearch } from "./FloatingLibrarySearch.jsx";


function renderSearch(overrides = {}) {
  const onClear = vi.fn();
  render(<FloatingLibrarySearch
    expanded
    onChange={vi.fn()}
    onClear={onClear}
    onCommit={vi.fn()}
    onRevert={vi.fn()}
    onToggleExpanded={vi.fn()}
    value="akira"
    {...overrides}
  />);
  return { onClear };
}


describe("FloatingLibrarySearch clear affordance", () => {
  test("desktop interaction mode does not create a clear X", () => {
    renderSearch({ desktopInteractionMode: true });

    expect(screen.getByRole("searchbox", { name: "Search library" })).toHaveValue("akira");
    expect(screen.getByRole("searchbox", { name: "Search library" })).toHaveAttribute("type", "text");
    expect(screen.queryByRole("button", { name: "Clear search" })).not.toBeInTheDocument();
  });

  test("phone and tablet interaction mode keeps the explicit clear X", () => {
    const { onClear } = renderSearch({ desktopInteractionMode: false });

    fireEvent.click(screen.getByRole("button", { name: "Clear search" }));
    expect(onClear).toHaveBeenCalledWith("floating");
  });
});
