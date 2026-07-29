import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import {
  MemoryRouter,
  useLocation,
  useNavigationType,
} from "react-router-dom";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { rememberLibraryReturnTarget, readLibraryReturnTarget } from "../lib/libraryNavigation.js";
import { DesktopLibraryIsland, LIBRARY_SEARCH_DEBOUNCE_MS } from "./DesktopLibraryIsland.jsx";


function LocationProbe({ onLocation }) {
  const location = useLocation();
  const navigationType = useNavigationType();
  const value = `${location.pathname}${location.search}${location.hash}`;
  useEffect(() => {
    onLocation?.({
      navigationType,
      restoreLibraryReturn: Boolean(location.state?.restoreLibraryReturn),
      value,
    });
  }, [location.key, location.state, navigationType, onLocation, value]);
  return <output data-testid="location">{`${value}|${navigationType}`}</output>;
}


function renderIsland({
  initialEntry = "/library?category=movies",
  libraryState = {},
  onLogout = vi.fn(),
  onLocation,
  position = "top",
  user = {
    id: 2,
    username: "viewer",
    role: "standard_user",
    assistant_beta_enabled: false,
  },
} = {}) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <DesktopLibraryIsland
        libraryState={{
          availableGenres: ["Action", "Drama", "Fantasy"],
          prepareExit: vi.fn(),
          ...libraryState,
        }}
        onLogout={onLogout}
        position={position}
        user={user}
      />
      <LocationProbe onLocation={onLocation} />
    </MemoryRouter>,
  );
}


describe("DesktopLibraryIsland", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    vi.stubGlobal("ResizeObserver", class {
      observe() {}
      disconnect() {}
    });
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  test("renders the approved order with a non-clickable brand and active URL category", () => {
    renderIsland({ initialEntry: "/library?category=anime" });

    const nav = screen.getByRole("navigation", { name: "Library controls" });
    expect(within(nav).getByText("Elvern").tagName).toBe("SPAN");
    expect(within(nav).queryByRole("link")).not.toBeInTheDocument();
    expect(within(nav).getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Movies",
      "TV Shows",
      "Anime",
      "Cartoon",
    ]);
    expect(within(nav).getByRole("tab", { name: "Anime" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(within(nav).getByRole("searchbox", { name: "Search library" })).toHaveAttribute(
      "placeholder",
      "Search",
    );
  });

  test("keeps multi-filter changes draft-only and commits one canonical navigation", () => {
    const locations = [];
    renderIsland({
      initialEntry: "/library?category=movies&q=matrix#card",
      onLocation: (location) => locations.push(location),
    });

    fireEvent.click(screen.getByRole("button", { name: "Arrange library" }));
    const panel = screen.getByRole("dialog", { name: "Arrange library" });
    fireEvent.click(within(panel).getByRole("button", { name: "Local" }));
    fireEvent.click(within(panel).getByRole("button", { name: "Action" }));
    fireEvent.click(within(panel).getByRole("button", { name: "Drama" }));
    fireEvent.click(within(panel).getByRole("button", { name: "Diamond" }));
    fireEvent.click(within(panel).getByRole("button", { name: "Gold" }));
    fireEvent.click(within(panel).getByRole("button", { name: /Alphabetical/ }));
    fireEvent.click(within(panel).getByRole("button", { name: /Alphabetical/ }));

    expect(screen.getByTestId("location")).toHaveTextContent(
      "/library?category=movies&q=matrix#card",
    );
    expect(screen.getByRole("button", { name: "Arrange library" })).toHaveTextContent("6");

    fireEvent.click(within(panel).getByRole("button", { name: "Done" }));

    expect(screen.getByTestId("location")).toHaveTextContent(
      "/library?category=movies&q=matrix&source=local&genre=Action&genre=Drama"
      + "&quality=diamond&quality=gold&sort=za#card",
    );
    expect(locations.filter((entry) => entry.navigationType === "PUSH")).toHaveLength(1);
  });

  test("Reset changes only the draft and an unchanged close does not navigate", () => {
    const locations = [];
    renderIsland({
      initialEntry: "/library?category=movies",
      onLocation: (location) => locations.push(location),
    });
    fireEvent.click(screen.getByRole("button", { name: "Arrange library" }));
    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    fireEvent.click(screen.getByRole("button", { name: "Done" }));

    expect(screen.queryByRole("dialog", { name: "Arrange library" })).not.toBeInTheDocument();
    expect(locations.filter((entry) => entry.navigationType === "PUSH")).toHaveLength(0);
  });

  test.each([
    ["outside click", () => fireEvent.pointerDown(document.body)],
    ["Escape", () => fireEvent.keyDown(document, { key: "Escape" })],
  ])("%s commits a changed draft once", (_label, closePanel) => {
    const locations = [];
    renderIsland({ onLocation: (location) => locations.push(location) });
    fireEvent.click(screen.getByRole("button", { name: "Arrange library" }));
    fireEvent.click(screen.getByRole("button", { name: "Cloud" }));

    closePanel();

    expect(screen.getByTestId("location")).toHaveTextContent(
      "/library?category=movies&source=cloud",
    );
    expect(locations.filter((entry) => entry.navigationType === "PUSH")).toHaveLength(1);
  });

  test("Arrange and Avatar are mutually exclusive and preserve the pending draft", () => {
    renderIsland();
    fireEvent.click(screen.getByRole("button", { name: "Arrange library" }));
    fireEvent.click(screen.getByRole("button", { name: "Cloud" }));

    fireEvent.click(screen.getByRole("button", { name: "Account: viewer" }));

    expect(screen.queryByRole("dialog", { name: "Arrange library" })).not.toBeInTheDocument();
    expect(screen.getByRole("menu", { name: "Account menu" })).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/library?category=movies&source=cloud",
    );
  });

  test("search waits for the real debounce and replaces the current URL", () => {
    vi.useFakeTimers();
    renderIsland({
      initialEntry: "/library?category=anime&source=cloud&genre=Action",
    });
    const search = screen.getByRole("searchbox", { name: "Search library" });

    fireEvent.change(search, { target: { value: "akira" } });
    act(() => vi.advanceTimersByTime(LIBRARY_SEARCH_DEBOUNCE_MS - 1));
    expect(screen.getByTestId("location")).not.toHaveTextContent("q=akira");

    act(() => vi.advanceTimersByTime(1));
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/library?category=anime&source=cloud&genre=Action&q=akira|REPLACE",
    );
  });

  test("category and changed Arrange draft commit in one navigation", () => {
    const locations = [];
    renderIsland({ onLocation: (location) => locations.push(location) });
    fireEvent.click(screen.getByRole("button", { name: "Arrange library" }));
    fireEvent.click(screen.getByRole("button", { name: "Cloud" }));

    fireEvent.click(screen.getByRole("tab", { name: "Anime" }));

    expect(screen.getByTestId("location")).toHaveTextContent(
      "/library?category=anime&source=cloud",
    );
    expect(locations.filter((entry) => entry.navigationType === "PUSH")).toHaveLength(1);
  });

  test("the active Detail category performs exact return while another category drops restore state", () => {
    rememberLibraryReturnTarget({
      listPath: "/library?category=movies&source=cloud&q=phase",
      anchorItemId: 42,
      anchorInstanceKey: "other-movies:42",
      userId: 2,
      role: "standard_user",
    });
    const currentLocations = [];
    const first = renderIsland({
      initialEntry: "/library/42",
      onLocation: (location) => currentLocations.push(location),
    });

    fireEvent.click(screen.getByRole("tab", { name: "Movies" }));
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/library?category=movies&source=cloud&q=phase",
    );
    expect(currentLocations.at(-1).restoreLibraryReturn).toBe(true);
    expect(readLibraryReturnTarget({
      userId: 2,
      role: "standard_user",
    })?.pendingRestore).toBe(true);

    first.unmount();
    window.sessionStorage.clear();
    rememberLibraryReturnTarget({
      listPath: "/library?category=movies&source=cloud&q=phase",
      anchorItemId: 42,
      anchorInstanceKey: "other-movies:42",
      userId: 2,
      role: "standard_user",
    });
    const changedLocations = [];
    renderIsland({
      initialEntry: "/library/42",
      onLocation: (location) => changedLocations.push(location),
    });

    fireEvent.click(screen.getByRole("tab", { name: "Anime" }));
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/library?category=anime&source=cloud&q=phase",
    );
    expect(changedLocations.at(-1).restoreLibraryReturn).toBe(false);
    expect(readLibraryReturnTarget({
      userId: 2,
      role: "standard_user",
    })?.pendingRestore).toBe(false);
  });

  test("admin Avatar menu has the approved destinations and accessible silver crown", () => {
    const prepareExit = vi.fn();
    renderIsland({
      libraryState: { prepareExit },
      user: {
        id: 1,
        username: "sectum",
        role: "admin",
        assistant_beta_enabled: false,
      },
    });

    fireEvent.click(screen.getByRole("button", { name: "Account: sectum" }));
    const menu = screen.getByRole("menu", { name: "Account menu" });
    expect(within(menu).getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "Settings",
      "Admin Panel",
      "Assistant",
      "Sign out",
    ]);
    expect(within(menu).getByLabelText("Administrator")).toBeInTheDocument();

    fireEvent.click(within(menu).getByRole("menuitem", { name: "Settings" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/settings");
    expect(prepareExit).toHaveBeenCalledWith("/library?category=movies");
  });

  test("standard Avatar menu respects Assistant permission and Sign out uses the supplied flow", () => {
    const onLogout = vi.fn();
    renderIsland({
      onLogout,
      user: {
        id: 2,
        username: "viewer",
        role: "standard_user",
        assistant_beta_enabled: true,
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Account: viewer" }));
    const menu = screen.getByRole("menu", { name: "Account menu" });
    expect(within(menu).queryByRole("menuitem", { name: "Admin Panel" })).not.toBeInTheDocument();
    expect(within(menu).getByRole("menuitem", { name: "Assistant" })).toBeInTheDocument();
    expect(within(menu).queryByLabelText("Administrator")).not.toBeInTheDocument();

    fireEvent.click(within(menu).getByRole("menuitem", { name: "Sign out" }));
    expect(onLogout).toHaveBeenCalledTimes(1);
  });

  test.each([
    ["top", "down"],
    ["bottom", "up"],
  ])("%s placement opens the Arrange panel %s and keeps every control present", (position, direction) => {
    renderIsland({ position });
    fireEvent.click(screen.getByRole("button", { name: "Arrange library" }));

    expect(screen.getByTestId("desktop-library-island")).toHaveClass(
      `desktop-library-island-wrap--${position}`,
    );
    expect(screen.getByRole("dialog", { name: "Arrange library" })).toHaveClass(
      `desktop-library-island__popover--${direction}`,
    );
    expect(screen.getAllByRole("tab")).toHaveLength(4);
    expect(screen.getByRole("searchbox", { name: "Search library" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Arrange library" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Account: viewer" })).toBeInTheDocument();
  });
});
