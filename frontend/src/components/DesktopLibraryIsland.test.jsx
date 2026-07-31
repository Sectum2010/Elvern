import {
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
import { readFileSync } from "node:fs";

import { rememberLibraryReturnTarget, readLibraryReturnTarget } from "../lib/libraryNavigation.js";
import { DesktopLibraryIsland } from "./DesktopLibraryIsland.jsx";


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
    vi.stubGlobal("PointerEvent", MouseEvent);
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
    fireEvent.click(within(panel).getByRole("radio", { name: "Local" }));
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
    fireEvent.click(screen.getByRole("radio", { name: "Cloud" }));

    closePanel();

    expect(screen.getByTestId("location")).toHaveTextContent(
      "/library?category=movies&source=cloud",
    );
    expect(locations.filter((entry) => entry.navigationType === "PUSH")).toHaveLength(1);
  });

  test("Arrange and Avatar are mutually exclusive and preserve the pending draft", () => {
    renderIsland();
    fireEvent.click(screen.getByRole("button", { name: "Arrange library" }));
    fireEvent.click(screen.getByRole("radio", { name: "Cloud" }));

    fireEvent.click(screen.getByRole("button", { name: "Account: viewer" }));

    expect(screen.queryByRole("dialog", { name: "Arrange library" })).not.toBeInTheDocument();
    expect(screen.getByRole("menu", { name: "Account menu" })).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/library?category=movies&source=cloud",
    );
  });

  test("search remains a local draft until Enter and then replaces the current URL", () => {
    renderIsland({
      initialEntry: "/library?category=anime&source=cloud&genre=Action",
    });
    const search = screen.getByRole("searchbox", { name: "Search library" });

    fireEvent.change(search, { target: { value: "akira" } });
    expect(screen.getByTestId("location")).not.toHaveTextContent("q=akira");
    expect(search).toHaveValue("akira");

    fireEvent.keyDown(search, { key: "Enter" });
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/library?category=anime&source=cloud&genre=Action&q=akira|REPLACE",
    );
  });

  test("view actions discard an unsubmitted search and use the committed query", () => {
    const prepareExit = vi.fn();
    renderIsland({ libraryState: { prepareExit } });
    const search = screen.getByRole("searchbox", { name: "Search library" });

    fireEvent.change(search, { target: { value: "draft only" } });
    fireEvent.click(screen.getByRole("tab", { name: "Anime" }));

    expect(search).toHaveValue("");
    expect(screen.getByTestId("location")).toHaveTextContent("/library?category=anime");
    expect(screen.getByTestId("location")).not.toHaveTextContent("q=draft");

    fireEvent.click(screen.getByRole("button", { name: "Arrange library" }));
    fireEvent.click(screen.getByRole("radio", { name: "Cloud" }));
    fireEvent.click(screen.getByRole("button", { name: "Done" }));

    expect(search).toHaveValue("");
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/library?category=anime&source=cloud",
    );
    expect(screen.getByTestId("location")).not.toHaveTextContent("q=draft");

    fireEvent.click(screen.getByRole("button", { name: "Account: viewer" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Settings" }));
    expect(prepareExit).toHaveBeenCalledWith("/library?category=anime&source=cloud");
  });

  test("Escape restores the committed search without navigating", () => {
    renderIsland({ initialEntry: "/library?category=movies&q=matrix" });
    const search = screen.getByRole("searchbox", { name: "Search library" });

    fireEvent.change(search, { target: { value: "unsubmitted" } });
    fireEvent.keyDown(search, { key: "Escape" });

    expect(search).toHaveValue("matrix");
    expect(search).not.toHaveFocus();
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/library?category=movies&q=matrix",
    );
  });

  test("source is click-only with roving keyboard radio behavior", () => {
    renderIsland();
    fireEvent.click(screen.getByRole("button", { name: "Arrange library" }));
    const sourceControl = screen.getByRole("radiogroup", { name: "Library source" });
    const all = within(sourceControl).getByRole("radio", { name: "All" });
    const local = within(sourceControl).getByRole("radio", { name: "Local" });
    const cloud = within(sourceControl).getByRole("radio", { name: "Cloud" });

    expect(all).toHaveAttribute("tabindex", "0");
    expect(local).toHaveAttribute("tabindex", "-1");
    expect(all).not.toHaveAttribute("onpointerdown");

    all.focus();
    fireEvent.keyDown(all, { key: "ArrowRight" });
    expect(local).toHaveFocus();
    expect(local).toHaveAttribute("aria-checked", "true");

    fireEvent.keyDown(local, { key: "End" });
    expect(cloud).toHaveFocus();
    expect(cloud).toHaveAttribute("aria-checked", "true");

    fireEvent.keyDown(cloud, { key: "Home" });
    expect(all).toHaveFocus();
    expect(all).toHaveAttribute("aria-checked", "true");

    fireEvent.click(cloud);
    expect(cloud).toHaveAttribute("aria-checked", "true");
  });

  test("clearing a committed query navigates immediately but clearing a local draft does not", () => {
    const locations = [];
    renderIsland({
      initialEntry: "/library?category=movies&q=matrix",
      onLocation: (location) => locations.push(location),
    });
    const search = screen.getByRole("searchbox", { name: "Search library" });

    fireEvent.change(search, { target: { value: "   " } });
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/library?category=movies|REPLACE",
    );

    fireEvent.change(search, { target: { value: "draft" } });
    fireEvent.change(search, { target: { value: "" } });
    expect(locations.filter((entry) => entry.navigationType === "REPLACE")).toHaveLength(1);
  });

  test("blur restores the committed query without applying a draft", () => {
    renderIsland({ initialEntry: "/library?category=movies&q=matrix" });
    const search = screen.getByRole("searchbox", { name: "Search library" });

    fireEvent.focus(search);
    fireEvent.change(search, { target: { value: "draft" } });
    fireEvent.blur(search);

    expect(search).toHaveValue("matrix");
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/library?category=movies&q=matrix",
    );
  });

  test("IME composition never commits or clears an intermediate value", () => {
    renderIsland({ initialEntry: "/library?category=movies&q=matrix" });
    const search = screen.getByRole("searchbox", { name: "Search library" });

    fireEvent.compositionStart(search);
    fireEvent.change(search, { target: { value: "" } });
    fireEvent.keyDown(search, {
      key: "Enter",
      keyCode: 229,
      isComposing: true,
    });
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/library?category=movies&q=matrix",
    );

    fireEvent.compositionEnd(search, { data: "" });
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/library?category=movies|REPLACE",
    );
  });

  test("IME completion after blur restores the committed query", () => {
    renderIsland({ initialEntry: "/library?category=movies&q=matrix" });
    const search = screen.getByRole("searchbox", { name: "Search library" });

    search.focus();
    fireEvent.compositionStart(search);
    fireEvent.change(search, { target: { value: "日本語" } });
    fireEvent.blur(search);
    fireEvent.compositionEnd(search, { data: "日本語" });

    expect(search).toHaveValue("matrix");
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/library?category=movies&q=matrix",
    );
  });

  test("expanded genres provide an adjacent Collapse action", () => {
    renderIsland({
      libraryState: {
        availableGenres: [
          "Action",
          "Adventure",
          "Animation",
          "Comedy",
          "Crime",
          "Documentary",
          "Drama",
          "Family",
          "Fantasy",
          "Horror",
        ],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Arrange library" }));

    fireEvent.click(screen.getByRole("button", { name: "+ 2 more" }));
    expect(screen.getByRole("button", { name: "Collapse" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Horror" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Collapse" }));
    expect(screen.queryByRole("button", { name: "Horror" })).not.toBeInTheDocument();
  });

  test("category and changed Arrange draft commit in one navigation", () => {
    const locations = [];
    renderIsland({ onLocation: (location) => locations.push(location) });
    fireEvent.click(screen.getByRole("button", { name: "Arrange library" }));
    fireEvent.click(screen.getByRole("radio", { name: "Cloud" }));

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
    expect(within(menu).getByLabelText("Administrator").parentElement).toHaveClass(
      "desktop-library-island__identity",
    );

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

  test("desktop Island CSS keeps a stable substrate and immediate selection feedback", () => {
    const styles = readFileSync(`${process.cwd()}/src/styles.css`, "utf8");

    expect(styles).toMatch(
      /\.desktop-library-island\s*\{[^}]*background:\s*rgba\(10,\s*14,\s*22,\s*0\.84\);/s,
    );
    expect(styles).toMatch(
      /\.desktop-library-island__chips button\.is-active:hover,[\s\S]*?background:\s*#e8ecf2;/,
    );
    expect(styles).toMatch(
      /\.desktop-library-island__menu-items \.desktop-library-island__sign-out:hover,[\s\S]*?color:\s*#ffd0d0;/,
    );
    expect(styles).toMatch(
      /\.desktop-library-island__sort-list button:hover,[\s\S]*?background:\s*rgba\(255,\s*255,\s*255,\s*0\.09\);/,
    );
    expect(styles).toMatch(
      /\.desktop-library-island__arrange-footer button:hover,[\s\S]*?color:\s*#fff;/,
    );
  });
});
