import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { MediaCard } from "./MediaCard";
import {
  clampPosterContextMenuPosition,
  PosterContextMenuProvider,
} from "./PosterContextMenu";


const smartPosterMocks = vi.hoisted(() => ({
  register: vi.fn(),
  subscribe: vi.fn(() => () => {}),
}));


vi.mock("../lib/smartPosterLoading", () => ({
  getSmartPosterCardSnapshot: () => ({ mode: "attach" }),
  isSmartPosterLoadingSupported: () => document.documentElement.dataset.deviceShell === "iphone",
  markSmartPosterCardError: vi.fn(),
  markSmartPosterCardLoaded: vi.fn(),
  POSTER_MODE_ATTACH: "attach",
  registerSmartPosterCard: smartPosterMocks.register,
  subscribeSmartPosterCard: smartPosterMocks.subscribe,
  unregisterSmartPosterCard: vi.fn(),
}));


function renderCard({
  deviceShell = "desktop",
  smartPosterLoadingEnabled = true,
  posterMenuEnabled = false,
  item = {},
} = {}) {
  document.documentElement.dataset.deviceShell = deviceShell;
  render(
    <MemoryRouter>
      <PosterContextMenuProvider enabled={posterMenuEnabled}>
        <MediaCard
          item={{
            id: 42,
            title: "Akira",
            source_kind: "local",
            poster_url: "/api/library/item/42/poster?v=cache-token&variant=original#poster",
            ...item,
          }}
          smartPosterLoadingEnabled={smartPosterLoadingEnabled}
        />
      </PosterContextMenuProvider>
    </MemoryRouter>,
  );
  return document.querySelector(".media-card__poster-image");
}


describe("MediaCard poster loading", () => {
  beforeEach(() => {
    smartPosterMocks.register.mockClear();
    smartPosterMocks.subscribe.mockClear();
  });

  afterEach(() => {
    cleanup();
    delete document.documentElement.dataset.deviceShell;
  });

  test.each(["desktop", "android", "ipad"])(
    "%s requests the card variant without enabling the smart scheduler",
    (deviceShell) => {
      const image = renderCard({ deviceShell });

      expect(image).toHaveAttribute(
        "src",
        "/api/library/item/42/poster?v=cache-token&variant=card#poster",
      );
      expect(image).toHaveAttribute("loading", "lazy");
      expect(image).toHaveAttribute("decoding", "async");
      expect(smartPosterMocks.register).not.toHaveBeenCalled();
    },
  );

  test("iPhone keeps smart scheduler admission while using the same card variant", () => {
    const image = renderCard({ deviceShell: "iphone" });

    expect(image).toHaveAttribute(
      "src",
      "/api/library/item/42/poster?v=cache-token&variant=card#poster",
    );
    expect(image).toHaveAttribute("loading", "eager");
    expect(smartPosterMocks.register).toHaveBeenCalledTimes(1);
    expect(smartPosterMocks.subscribe).toHaveBeenCalledTimes(1);
  });
});


describe("MediaCard shared poster context menu", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  test.each([
    ["poster image", {}],
    ["letter fallback", { poster_url: null }],
  ])("desktop right click opens the shared menu from the %s", (_label, item) => {
    renderCard({ posterMenuEnabled: true, item });
    const poster = document.querySelector(".media-card__poster");
    const contextEvent = new MouseEvent("contextmenu", {
      bubbles: true,
      cancelable: true,
      clientX: 120,
      clientY: 90,
    });

    fireEvent(poster, contextEvent);

    expect(contextEvent.defaultPrevented).toBe(true);
    expect(screen.getByRole("menu", { name: "Poster actions for Akira" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Edit" })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: "Generate" })).toBeInTheDocument();
    expect(screen.queryByText(/beta/i)).not.toBeInTheDocument();
  });

  test("menu coordinates are clamped away from the viewport edges", () => {
    expect(clampPosterContextMenuPosition({
      clientX: 318,
      clientY: 238,
      menuWidth: 176,
      menuHeight: 96,
      viewportWidth: 320,
      viewportHeight: 240,
    })).toEqual({ left: 136, top: 136 });
  });

  test.each([
    ["card body", ".media-card__body"],
    ["title", ".media-card__title"],
    ["page background", "body"],
  ])("%s keeps the native context menu", (_label, selector) => {
    renderCard({ posterMenuEnabled: true });
    const contextEvent = new MouseEvent("contextmenu", { bubbles: true, cancelable: true });

    document.querySelector(selector).dispatchEvent(contextEvent);

    expect(contextEvent.defaultPrevented).toBe(false);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  test("phone and tablet mode neither opens nor prevents the native context menu", () => {
    renderCard({ posterMenuEnabled: false });
    const contextEvent = new MouseEvent("contextmenu", { bubbles: true, cancelable: true });

    document.querySelector(".media-card__poster").dispatchEvent(contextEvent);

    expect(contextEvent.defaultPrevented).toBe(false);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  test("placeholder actions stay inert until an outside click closes the menu", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderCard({ posterMenuEnabled: true });
    fireEvent.contextMenu(document.querySelector(".media-card__poster"), { clientX: 40, clientY: 50 });

    fireEvent.click(screen.getByRole("menuitem", { name: "Edit" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Generate" }));

    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  test("right clicking a second poster rebinds the one shared menu", () => {
    render(
      <MemoryRouter>
        <PosterContextMenuProvider enabled>
          <MediaCard item={{ id: 1, title: "Akira", source_kind: "local", poster_url: null }} />
          <MediaCard item={{ id: 2, title: "Arrival", source_kind: "local", poster_url: null }} />
        </PosterContextMenuProvider>
      </MemoryRouter>,
    );
    const posters = document.querySelectorAll(".media-card__poster");

    fireEvent.contextMenu(posters[0], { clientX: 12, clientY: 14 });
    expect(screen.getByRole("menu", { name: "Poster actions for Akira" })).toBeInTheDocument();

    fireEvent.contextMenu(posters[1], { clientX: 220, clientY: 180 });
    expect(screen.queryByRole("menu", { name: "Poster actions for Akira" })).not.toBeInTheDocument();
    expect(screen.getByRole("menu", { name: "Poster actions for Arrival" })).toBeInTheDocument();
    expect(document.querySelectorAll(".poster-context-menu")).toHaveLength(1);
  });
});
