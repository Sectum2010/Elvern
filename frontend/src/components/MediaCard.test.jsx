import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { MediaCard } from "./MediaCard";


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


function renderCard({ deviceShell = "desktop", smartPosterLoadingEnabled = true } = {}) {
  document.documentElement.dataset.deviceShell = deviceShell;
  render(
    <MemoryRouter>
      <MediaCard
        item={{
          id: 42,
          title: "Akira",
          source_kind: "local",
          poster_url: "/api/library/item/42/poster?v=cache-token&variant=original#poster",
        }}
        smartPosterLoadingEnabled={smartPosterLoadingEnabled}
      />
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
