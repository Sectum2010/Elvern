import { cleanup, render, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { apiRequest } from "../lib/api";
import { LibrarySourcePage } from "./LibrarySourcePage";


vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ refreshAuth: vi.fn() }),
}));

vi.mock("../lib/api", () => ({
  apiRequest: vi.fn(),
}));


describe("LibrarySourcePage poster URLs", () => {
  beforeEach(() => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn(() => ({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    });
    apiRequest.mockImplementation((path) => {
      if (path === "/api/user-settings") {
        return Promise.resolve({ floating_library_search_enabled: true });
      }
      if (path === "/api/library") {
        return Promise.resolve({
          items: [{
            id: 42,
            title: "Akira",
            source_kind: "local",
            poster_url: "/api/library/item/42/poster?v=cache-token#poster",
          }],
          series_rails: [],
          cloud_series_rails: [],
        });
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`));
    });
  });

  afterEach(() => {
    cleanup();
    apiRequest.mockReset();
    vi.restoreAllMocks();
  });

  test("local source cards request the existing card poster variant", async () => {
    render(
      <MemoryRouter initialEntries={["/library/local"]}>
        <LibrarySourcePage sourceKind="local" />
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(document.querySelector(".media-card__poster-image")).toHaveAttribute(
        "src",
        "/api/library/item/42/poster?v=cache-token&variant=card#poster",
      );
    });
  });
});
