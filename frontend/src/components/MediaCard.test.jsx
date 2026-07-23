import { readFileSync } from "node:fs";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { MediaCard } from "./MediaCard";
import {
  clampPosterContextMenuPosition,
  PosterContextMenuProvider,
} from "./PosterContextMenu";
import {
  POSTER_RECOVERY_COOLDOWN_MS,
  resetPosterRecoveryStateForTests,
} from "../lib/posterRecovery";
import {
  CONNECTIVITY_RECOVERED_EVENT,
  STARTUP_CONNECTIVITY_FAILURE_EVENT,
} from "../lib/startupConnection";


const smartPosterMocks = vi.hoisted(() => ({
  register: vi.fn(),
  retry: vi.fn(),
  subscribe: vi.fn(() => () => {}),
}));


vi.mock("../lib/smartPosterLoading", () => ({
  getSmartPosterCardSnapshot: () => ({ mode: "attach" }),
  isSmartPosterLoadingSupported: () => document.documentElement.dataset.deviceShell === "iphone",
  markSmartPosterCardError: vi.fn(),
  markSmartPosterCardLoaded: vi.fn(),
  POSTER_MODE_ATTACH: "attach",
  registerSmartPosterCard: smartPosterMocks.register,
  retrySmartPosterCard: smartPosterMocks.retry,
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
  test("gives non-scheduler poster downloads low fetch priority", () => {
    const image = renderCard({ smartPosterLoadingEnabled: false });

    expect(image).toHaveAttribute("fetchpriority", "low");
  });

  beforeEach(() => {
    resetPosterRecoveryStateForTests();
    smartPosterMocks.register.mockClear();
    smartPosterMocks.retry.mockClear();
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
        "/api/library/item/42/poster?v=cache-token&variant=card&display_width=1400#poster",
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
      "/api/library/item/42/poster?v=cache-token&variant=card&display_width=1400#poster",
    );
    expect(image).toHaveAttribute("loading", "eager");
    expect(smartPosterMocks.register).toHaveBeenCalledTimes(1);
    expect(smartPosterMocks.subscribe).toHaveBeenCalledTimes(1);
  });

  test("keeps the letter fallback visible until the poster loads", () => {
    const image = renderCard();
    const fallback = document.querySelector(".media-card__poster-fallback");

    expect(fallback).not.toHaveClass("media-card__poster-fallback--hidden");
    expect(image).not.toHaveClass("media-card__poster-image--loaded");

    fireEvent.load(image);

    expect(fallback).toHaveClass("media-card__poster-fallback--hidden");
    expect(image).toHaveClass("media-card__poster-image--loaded");
  });

  test("keeps fallback after an error and retries only when the resolved URL changes", async () => {
    const item = {
      id: 42,
      title: "Akira",
      source_kind: "local",
      poster_url: "/api/library/item/42/poster?v=first",
    };
    const rendered = render(
      <MemoryRouter>
        <PosterContextMenuProvider enabled={false}>
          <MediaCard item={item} />
        </PosterContextMenuProvider>
      </MemoryRouter>,
    );
    fireEvent.error(document.querySelector(".media-card__poster-image"));

    expect(document.querySelector(".media-card__poster-image")).not.toBeInTheDocument();
    expect(document.querySelector(".media-card__poster-fallback")).not.toHaveClass(
      "media-card__poster-fallback--hidden",
    );

    rendered.rerender(
      <MemoryRouter>
        <PosterContextMenuProvider enabled={false}>
          <MediaCard item={item} />
        </PosterContextMenuProvider>
      </MemoryRouter>,
    );
    expect(document.querySelector(".media-card__poster-image")).not.toBeInTheDocument();

    rendered.rerender(
      <MemoryRouter>
        <PosterContextMenuProvider enabled={false}>
          <MediaCard item={{ ...item, poster_url: "/api/library/item/42/poster?v=second" }} />
        </PosterContextMenuProvider>
      </MemoryRouter>,
    );
    await waitFor(() => expect(document.querySelector(".media-card__poster-image")).toHaveAttribute(
      "src",
      "/api/library/item/42/poster?v=second&variant=card&display_width=1400",
    ));
  });

  test("retries a transport-failed poster once after connectivity recovers", async () => {
    vi.useFakeTimers();
    try {
      const image = renderCard({ deviceShell: "iphone" });
      fireEvent(window, new CustomEvent(STARTUP_CONNECTIVITY_FAILURE_EVENT, {
        detail: { classification: "transport" },
      }));
      fireEvent.error(image);

      expect(document.querySelector(".media-card__poster-image")).not.toBeInTheDocument();

      fireEvent(window, new CustomEvent(CONNECTIVITY_RECOVERED_EVENT, {
        detail: { generation: 7 },
      }));
      await vi.advanceTimersByTimeAsync(499);
      expect(document.querySelector(".media-card__poster-image")).not.toBeInTheDocument();

      await vi.advanceTimersByTimeAsync(1);
      expect(document.querySelector(".media-card__poster-image")).toHaveAttribute(
        "src",
        "/api/library/item/42/poster?v=cache-token&variant=card&display_width=1400#poster",
      );
      expect(smartPosterMocks.retry).toHaveBeenCalledTimes(1);

      fireEvent(window, new CustomEvent(STARTUP_CONNECTIVITY_FAILURE_EVENT, {
        detail: { classification: "transport" },
      }));
      fireEvent.error(document.querySelector(".media-card__poster-image"));
      fireEvent(window, new CustomEvent(CONNECTIVITY_RECOVERED_EVENT, {
        detail: { generation: 8 },
      }));
      await vi.advanceTimersByTimeAsync(500);
      expect(document.querySelector(".media-card__poster-image")).not.toBeInTheDocument();
      expect(smartPosterMocks.retry).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  test("retries a non-scheduler poster once without changing its URL", async () => {
    vi.useFakeTimers();
    try {
      const image = renderCard();
      fireEvent(window, new CustomEvent(STARTUP_CONNECTIVITY_FAILURE_EVENT, {
        detail: { classification: "transport" },
      }));
      fireEvent.error(image);
      fireEvent(window, new CustomEvent(CONNECTIVITY_RECOVERED_EVENT, {
        detail: { generation: 8 },
      }));
      await vi.advanceTimersByTimeAsync(POSTER_RECOVERY_COOLDOWN_MS);

      expect(document.querySelector(".media-card__poster-image")).toHaveAttribute(
        "src",
        "/api/library/item/42/poster?v=cache-token&variant=card&display_width=1400#poster",
      );
      expect(smartPosterMocks.retry).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  test("changing display width creates a new src and resets loaded state", async () => {
    const item = {
      id: 42,
      title: "Akira",
      source_kind: "local",
      poster_url: "/api/library/item/42/poster?v=cache-token",
    };
    const rendered = render(
      <MemoryRouter>
        <PosterContextMenuProvider enabled={false}>
          <MediaCard item={item} posterDisplayWidth="800" />
        </PosterContextMenuProvider>
      </MemoryRouter>,
    );
    fireEvent.load(document.querySelector(".media-card__poster-image"));
    expect(document.querySelector(".media-card__poster-image")).toHaveClass("media-card__poster-image--loaded");

    rendered.rerender(
      <MemoryRouter>
        <PosterContextMenuProvider enabled={false}>
          <MediaCard item={item} posterDisplayWidth="1400" />
        </PosterContextMenuProvider>
      </MemoryRouter>,
    );

    const nextImage = document.querySelector(".media-card__poster-image");
    expect(nextImage).toHaveAttribute(
      "src",
      "/api/library/item/42/poster?v=cache-token&variant=card&display_width=1400",
    );
    expect(nextImage).not.toHaveClass("media-card__poster-image--loaded");
    expect(document.querySelector(".media-card__poster-fallback")).not.toHaveClass(
      "media-card__poster-fallback--hidden",
    );
  });

  test("uses the authoritative v2 quality rank when the item provides one", () => {
    renderCard({
      item: {
        quality_rank: {
          key: "gold",
          label: "Server Gold",
          score: 11,
          description: "Server-provided quality description.",
          detected: ["server"],
          tooltip: "Server-provided quality tooltip.",
        },
      },
    });

    expect(screen.getByRole("button", {
      name: "Server Gold: Server-provided quality description.",
    })).toHaveTextContent("Server Gold");
    expect(screen.getByRole("tooltip")).toHaveTextContent("Server-provided quality tooltip.");
  });

  test("uses the server quality tier when connected to an older v1 response", () => {
    renderCard({
      item: {
        quality_tier: "diamond",
      },
    });

    expect(screen.getByRole("button", { name: /^Diamond:/ })).toHaveTextContent("Diamond");
  });

  test("keeps the existing client quality rank as the final legacy fallback", () => {
    renderCard({
      item: {
        original_filename: "Akira.1988.2160p.REMUX.TrueHD.Atmos.HEVC.mkv",
        width: 3840,
        height: 2160,
        video_codec: "hevc",
        audio_codec: "truehd",
        container: "mkv",
        file_size: 80 * 1024 * 1024 * 1024,
      },
    });

    expect(screen.getByRole("button", { name: /^Diamond:/ })).toHaveTextContent("Diamond");
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

  test("pencil keeps the lucide 45 degree slant without a corrective rotation", () => {
    const styles = readFileSync(`${process.cwd()}/src/styles.css`, "utf8");

    expect(styles).not.toMatch(/\.poster-context-menu__pencil\s*\{[^}]*transform:/s);
  });

  test("menu follows its poster point out of view and returns to the same point", async () => {
    renderCard({ posterMenuEnabled: true });
    const poster = document.querySelector(".media-card__poster");
    let posterTop = 200;
    vi.spyOn(poster, "getBoundingClientRect").mockImplementation(() => ({
      left: 50,
      right: 250,
      top: posterTop,
      bottom: posterTop + 300,
      width: 200,
      height: 300,
      x: 50,
      y: posterTop,
      toJSON: () => ({}),
    }));

    fireEvent.contextMenu(poster, { clientX: 100, clientY: 260 });
    const menu = screen.getByRole("menu", { name: "Poster actions for Akira" });
    expect(menu.style.top).toBe("260px");

    posterTop = -400;
    fireEvent.scroll(window);
    await waitFor(() => expect(menu.style.top).toBe("-340px"));
    expect(menu).toBeInTheDocument();

    posterTop = 200;
    fireEvent.scroll(window);
    await waitFor(() => expect(menu.style.top).toBe("260px"));
    expect(menu).toBeInTheDocument();
  });

  test("menu follows its poster through a nested horizontal rail scroll", async () => {
    renderCard({ posterMenuEnabled: true });
    const poster = document.querySelector(".media-card__poster");
    const rail = document.createElement("div");
    document.body.appendChild(rail);
    let posterLeft = 200;
    vi.spyOn(poster, "getBoundingClientRect").mockImplementation(() => ({
      left: posterLeft,
      right: posterLeft + 200,
      top: 100,
      bottom: 400,
      width: 200,
      height: 300,
      x: posterLeft,
      y: 100,
      toJSON: () => ({}),
    }));

    fireEvent.contextMenu(poster, { clientX: 250, clientY: 160 });
    const menu = screen.getByRole("menu", { name: "Poster actions for Akira" });
    expect(menu.style.left).toBe("250px");

    posterLeft = 20;
    fireEvent.scroll(rail);
    await waitFor(() => expect(menu.style.left).toBe("70px"));
    expect(menu).toBeInTheDocument();
    rail.remove();
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
