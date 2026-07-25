import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { fireEvent, render as testingLibraryRender, screen, waitFor, within } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigationType,
} from "react-router-dom";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { ApiNetworkError, ApiResponseError, apiRequest } from "../lib/api";
import {
  buildBackgroundPreviewStyle,
  deriveGradientEndFromSingleColor,
} from "../lib/userBackground";
import { SettingsPage } from "./SettingsPage";
import { queryClient } from "../lib/queryClient";
import { buildUserSettingsQueryKey } from "../lib/userSettingsQueries";

const mockAuthState = vi.hoisted(() => ({ id: 7, role: "standard_user" }));
const mockPlatformState = vi.hoisted(() => ({ deviceClass: "desktop" }));
const mockInstallState = vi.hoisted(() => ({ renders: 0 }));

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    user: {
      id: mockAuthState.id,
      username: "display-user",
      role: mockAuthState.role,
    },
  }),
}));

vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal()),
  apiRequest: vi.fn(),
}));

vi.mock("../features/install/InstallSettingsPanel", () => ({
  InstallSettingsPanel: () => {
    mockInstallState.renders += 1;
    return <div>Complete install panel</div>;
  },
}));

vi.mock("../lib/platformDetection", async (importOriginal) => ({
  ...(await importOriginal()),
  detectClientDeviceClass: () => mockPlatformState.deviceClass,
}));

const pagesDir = path.dirname(fileURLToPath(import.meta.url));
const settingsPagePath = path.resolve(pagesDir, "SettingsPage.jsx");
const shellLayoutPath = path.resolve(pagesDir, "../components/ShellLayout.jsx");
const stylesPath = path.resolve(pagesDir, "../styles.css");

const defaultSettings = {
  hide_duplicate_movies: true,
  hide_recently_added: false,
  floating_library_search_enabled: true,
  poster_card_appearance: "classic",
  poster_card_display_max_width: "1400",
  background_mode: "preset",
  background_preset: "neon",
  background_gradient_start: "#74114f",
  background_gradient_end: "#1b41b5",
  background_gradient_accent: "#5c1867",
  background_solid_color: "#151a21",
  background_photo_url: null,
  media_library_reference_private_value: null,
  media_library_reference_shared_default_value: "",
  media_library_reference_effective_value: "",
  media_library_reference_effective_source: "shared_default",
  media_library_reference_effective_label: "Shared default",
};


function render(ui, options) {
  return testingLibraryRender(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
    options,
  );
}

function LocationProbe() {
  const location = useLocation();
  const navigationType = useNavigationType();
  return (
    <p data-testid="settings-location">
      {`${location.pathname}${location.search}${location.hash}|${navigationType}|${location.state?.marker || ""}`}
    </p>
  );
}

function mockApi(initialSettings = defaultSettings, mockOptions = {}) {
  let settings = { ...initialSettings };
  let hiddenItems = [...(mockOptions.hiddenItems || [])];
  let globalHiddenItems = [...(mockOptions.globalHiddenItems || [])];
  let ageGroupItems = mockOptions.ageGroupItems || [
    {
      age_group_key: "age:title:galaxy|2026",
      display_title: "Galaxy",
      year: 2026,
      age_requirement: 18,
      age_requirement_display: "18+",
      copies_count: 2,
      auto_count: 1,
      manual_links_count: 1,
      primary_media_item_id: 42,
    },
    {
      age_group_key: "age:title:gentle cartoon|2024",
      display_title: "Gentle Cartoon",
      year: 2024,
      age_requirement: 6,
      age_requirement_display: "6",
      copies_count: 3,
      auto_count: 3,
      manual_links_count: 0,
      primary_media_item_id: 52,
    },
    {
      age_group_key: "age:title:unrestricted|2023",
      display_title: "Unrestricted",
      year: 2023,
      age_requirement: null,
      age_requirement_display: "None",
      copies_count: 4,
      auto_count: 4,
      manual_links_count: 0,
      primary_media_item_id: 62,
    },
  ];
  apiRequest.mockImplementation((requestPath, options = {}) => {
    if (requestPath === "/api/user-settings" && !options.method) {
      return Promise.resolve(settings);
    }
    if (requestPath === "/api/user-settings" && options.method === "PATCH") {
      settings = { ...settings, ...options.data };
      return Promise.resolve(settings);
    }
    if (requestPath === "/api/user-settings/background-photo" && options.method === "POST") {
      settings = {
        ...settings,
        background_mode: "photo",
        background_photo_url: "/api/user-settings/background-photo?v=123",
      };
      return Promise.resolve(settings);
    }
    if (requestPath === "/api/user-settings/background-photo" && options.method === "DELETE") {
      settings = {
        ...settings,
        background_mode: "preset",
        background_preset: "neon",
        background_photo_url: null,
      };
      return Promise.resolve(settings);
    }
    if (requestPath === "/api/user-hidden-items") {
      return Promise.resolve({ items: hiddenItems });
    }
    if (requestPath === "/api/admin/global-hidden-items") {
      return Promise.resolve({ items: globalHiddenItems });
    }
    if (
      requestPath.startsWith("/api/admin/hidden-items/")
      && requestPath.endsWith("/scope")
      && options.method === "PUT"
    ) {
      const itemId = Number(requestPath.split("/").at(-2));
      const targetScope = options.data?.target_scope;
      const sourceItems = targetScope === "global" ? hiddenItems : globalHiddenItems;
      const hiddenItem = sourceItems.find((item) => item.id === itemId) || {
        id: itemId,
        title: "Scope Movie",
        year: 2026,
        edition_label: null,
        poster_url: null,
      };
      const movedItem = { ...hiddenItem, hidden_at: "2026-07-24T00:00:00+00:00" };
      if (mockOptions.scopeCommitsBeforeFailure !== false) {
        if (targetScope === "global") {
          hiddenItems = hiddenItems.filter((item) => item.id !== itemId);
          globalHiddenItems = [movedItem, ...globalHiddenItems.filter((item) => item.id !== itemId)];
        } else {
          globalHiddenItems = globalHiddenItems.filter((item) => item.id !== itemId);
          hiddenItems = [movedItem, ...hiddenItems.filter((item) => item.id !== itemId)];
        }
      }
      if (mockOptions.scopeError === "network") {
        return Promise.reject(new ApiNetworkError());
      }
      if (mockOptions.scopeError === "malformed") {
        return Promise.reject(new ApiResponseError(200));
      }
      if (mockOptions.scopeError === "http") {
        return Promise.reject(Object.assign(new Error("Explicit server failure"), { status: 500 }));
      }
      return Promise.resolve({
        item_id: itemId,
        target_scope: targetScope,
        changed: true,
        hidden_at: movedItem.hidden_at,
        message: targetScope === "global"
          ? "This movie is hidden for everyone."
          : "This movie is now hidden only for your account.",
      });
    }
    if (requestPath === "/api/admin/media-library-reference") {
      return Promise.resolve({
        configured_value: "/srv/media",
        effective_value: "/srv/media",
        default_value: "/srv/media",
        configured_locations: ["/srv/media"],
        effective_locations: ["/srv/media"],
        category_summary: {
          movies: [{ path: "/srv/media/Movies -M", name: "Movies" }],
          tv: [],
          cartoon: [{ path: "/srv/media/Cartoons -C", name: "Cartoons" }],
          anime: [],
        },
        validation_rules: [
          "Choose one or more parent folders where Elvern should look for media folders.",
          "Elvern auto-discovers folders marked with -M, -TV, -AN, -C, -L, -S, and -X.",
          "Poster reference location stays manually configured below.",
        ],
      });
    }
    if (requestPath === "/api/admin/poster-reference-location") {
      return Promise.resolve({ configured_value: null, effective_value: "", default_value: "" });
    }
    if (requestPath.startsWith("/api/admin/local-directory-picker/capability")) {
      return Promise.resolve({
        native_picker_supported: true,
        same_host_linux: true,
        picker_backend: "zenity",
      });
    }
    if (requestPath === "/api/admin/local-directory-picker" && options.method === "POST") {
      const selectedPath = options.data?.purpose === "poster_reference"
        ? "/srv/posters/selected"
        : "/srv/media/selected-library";
      return Promise.resolve({
        status: "selected",
        selected_path: selectedPath,
        reason: null,
        picker_backend: "zenity",
      });
    }
    if (requestPath === "/api/admin/google-drive-setup") {
      return Promise.resolve({
        https_origin: "",
        client_id: "",
        client_secret: "",
        javascript_origin: "",
        redirect_uri: "",
        callback_source: "unconfigured",
        callback_warning: null,
        configuration_state: "not_configured",
        configuration_label: "Not configured",
        status_message: "",
        missing_fields: [],
        connected: false,
        account_email: null,
        account_name: null,
        instructions: [],
      });
    }
    if (requestPath === "/api/cloud-libraries") {
      return Promise.resolve({
        google: { enabled: false, connected: false },
        my_libraries: [],
        shared_libraries: [],
      });
    }
    if (requestPath === "/api/library/age-groups") {
      return Promise.resolve({
        total: ageGroupItems.length,
        items: ageGroupItems,
      });
    }
    if (requestPath === "/api/library/age-groups/age%3Atitle%3Agalaxy%7C2026") {
      return Promise.resolve({
        age_group_key: "age:title:galaxy|2026",
        display_title: "Galaxy",
        year: 2026,
        age_requirement: 18,
        age_requirement_display: "18+",
        copies_count: 2,
        manual_links_count: 1,
        primary_media_item_id: 42,
        auto_matched_copies: [{ id: 42, title: "Galaxy", year: 2026, source_label: "DGX" }],
        manual_linked_copies: [{ id: 43, title: "Galaxy Extended", year: 2026, source_label: "Cloud" }],
      });
    }
    if (requestPath.startsWith("/api/library/age-groups/search")) {
      return Promise.resolve({
        items: [
          {
            id: 44,
            title: "Galaxy 3D",
            year: 2026,
            source_label: "DGX",
            automatic_age_group_key: "age:title:galaxy 3d|2026",
          },
        ],
      });
    }
    if (requestPath === "/api/library/age-groups/link" && options.method === "POST") {
      return Promise.resolve({
        linked: true,
        age_group: {
          age_group_key: "age:title:galaxy|2026",
          display_title: "Galaxy",
          year: 2026,
          age_requirement: 18,
          age_requirement_display: "18+",
          copies_count: 2,
          manual_links_count: 1,
          primary_media_item_id: 42,
          auto_matched_copies: [{ id: 42, title: "Galaxy", year: 2026, source_label: "DGX" }],
          manual_linked_copies: [{ id: 44, title: "Galaxy 3D", year: 2026, source_label: "DGX" }],
        },
      });
    }
    if (requestPath.startsWith("/api/library/age-groups/links/") && options.method === "DELETE") {
      return Promise.resolve({ linked: false });
    }
    if (requestPath === "/api/library/item/42/age-requirement" && options.method === "PATCH") {
      ageGroupItems = ageGroupItems.map((group) => (
        group.age_group_key === "age:title:galaxy|2026"
          ? { ...group, age_requirement: null, age_requirement_display: "None" }
          : group
      ));
      return Promise.resolve({});
    }
    if (requestPath === "/api/auth/totp/status") {
      return Promise.resolve({ enabled: false, setup_available: false });
    }
    return Promise.resolve({});
  });
}

async function renderDisplaySettings(initialSettings = defaultSettings) {
  mockApi(initialSettings);
  render(
    <MemoryRouter>
      <SettingsPage />
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole("tab", { name: "Display" }));
  await screen.findByRole("heading", { name: "Background" });
}

beforeEach(() => {
  queryClient.clear();
  apiRequest.mockReset();
  mockAuthState.id = 7;
  mockAuthState.role = "standard_user";
  mockPlatformState.deviceClass = "desktop";
  mockInstallState.renders = 0;
  window.localStorage.clear();
});

describe("SettingsPage section navigation and consolidation", () => {
  test("shows the canonical tab order without a Hidden tab", () => {
    mockApi();
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>,
    );

    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Preferences",
      "Display",
      "Libraries",
      "Install",
      "Advanced",
    ]);
    expect(screen.queryByRole("tab", { name: "Hidden" })).not.toBeInTheDocument();

    const installIcon = screen.getByRole("tab", { name: "Install" }).querySelector("svg");
    expect(installIcon).toHaveAttribute("viewBox", "0 0 24 24");
    expect(installIcon?.innerHTML).toContain("M12 4v9");
    expect(installIcon?.innerHTML).toContain("M5 14.5v2.2");
  });

  test("clicking the active tab only toggles its expanded label without navigation", () => {
    mockApi();
    render(
      <MemoryRouter initialEntries={["/settings"]}>
        <LocationProbe />
        <SettingsPage />
      </MemoryRouter>,
    );

    const preferences = screen.getByRole("tab", { name: "Preferences" });
    fireEvent.click(preferences);

    expect(preferences).toHaveAttribute("aria-expanded", "true");
    expect(preferences).not.toHaveClass("admin-nav-card__button--expanded");
    expect(screen.getByTestId("settings-location")).toHaveTextContent("/settings|POP|");
  });

  test("canonicalizes the legacy Hidden URL before showing Libraries", async () => {
    mockApi();
    render(
      <MemoryRouter initialEntries={[{
        pathname: "/settings",
        search: "?other=1&section=hidden",
        hash: "#hidden-list",
        state: { marker: "preserved" },
      }]}>
        <Routes>
          <Route
            path="/settings"
            element={(
              <>
                <LocationProbe />
                <SettingsPage />
              </>
            )}
          />
        </Routes>
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByTestId("settings-location")).toHaveTextContent(
      "/settings?other=1&section=libraries#hidden-list|REPLACE|preserved",
    ));
    expect(screen.getByRole("tab", { name: "Libraries" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByRole("tab", { name: "Hidden" })).not.toBeInTheDocument();
  });

  test("section changes replace history while preserving query, hash, and state", async () => {
    mockApi();
    render(
      <MemoryRouter initialEntries={[{
        pathname: "/settings",
        search: "?source=bookmark&section=preferences",
        hash: "#oauth",
        state: { marker: "preserved" },
      }]}>
        <LocationProbe />
        <SettingsPage />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("tab", { name: "Install" }));

    await waitFor(() => expect(screen.getByTestId("settings-location")).toHaveTextContent(
      "/settings?source=bookmark&section=install#oauth|REPLACE|preserved",
    ));
  });

  test("direct Install access mounts only Install and skips unrelated ancillary requests", async () => {
    mockApi();
    render(
      <MemoryRouter initialEntries={["/settings?section=install"]}>
        <SettingsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Complete install panel")).toBeInTheDocument();
    await Promise.resolve();
    const unrelatedPaths = new Set([
      "/api/user-hidden-items",
      "/api/admin/global-hidden-items",
      "/api/cloud-libraries",
      "/api/admin/google-drive-setup",
      "/api/library/age-groups",
      "/api/admin/media-library-reference",
      "/api/admin/poster-reference-location",
      "/api/auth/totp/status",
    ]);
    expect(apiRequest.mock.calls.some(([requestPath]) => unrelatedPaths.has(requestPath))).toBe(false);
  });

  test("leaving Install starts ancillary loading once and does not keep Install mounted", async () => {
    mockApi();
    render(
      <MemoryRouter initialEntries={["/settings?section=install"]}>
        <SettingsPage />
      </MemoryRouter>,
    );
    expect(screen.getByText("Complete install panel")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Libraries" }));
    await screen.findByRole("heading", { name: "Library" });
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith(
      "/api/user-hidden-items",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ));
    expect(screen.queryByText("Complete install panel")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Install" }));
    fireEvent.click(screen.getByRole("tab", { name: "Libraries" }));
    await waitFor(() => {
      expect(apiRequest.mock.calls.filter(([path]) => path === "/api/user-hidden-items")).toHaveLength(1);
    });
  });

  test("Libraries keeps Shared and Hidden cards in the approved order for admins", async () => {
    mockAuthState.role = "admin";
    mockApi();
    render(
      <MemoryRouter initialEntries={["/settings?section=libraries"]}>
        <SettingsPage />
      </MemoryRouter>,
    );

    const shared = await screen.findByText("Shared Libraries");
    const personal = screen.getByText("Hidden for me");
    const global = screen.getByText("Hidden for everyone");
    const google = screen.getByText("Google Drive OAuth Setup");
    expect(shared.compareDocumentPosition(personal) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(personal.compareDocumentPosition(global) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(global.compareDocumentPosition(google) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(personal.closest("section")).not.toBe(global.closest("section"));
  });

  test("ordinary users see only their personal Hidden card", async () => {
    mockApi();
    render(
      <MemoryRouter initialEntries={["/settings?section=libraries"]}>
        <SettingsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Hidden for me")).toBeInTheDocument();
    expect(screen.queryByText("Hidden for everyone")).not.toBeInTheDocument();
    expect(apiRequest.mock.calls.some(([path]) => path === "/api/admin/global-hidden-items")).toBe(false);
  });

  test("a stale Hidden response from the previous identity cannot replace the new identity list", async () => {
    let resolveFirstHiddenRequest;
    const firstHiddenRequest = new Promise((resolve) => {
      resolveFirstHiddenRequest = resolve;
    });
    apiRequest.mockImplementation((requestPath) => {
      if (requestPath === "/api/user-settings") {
        return Promise.resolve(defaultSettings);
      }
      if (requestPath === "/api/user-hidden-items") {
        return mockAuthState.id === 7
          ? firstHiddenRequest
          : Promise.resolve({
            items: [{
              id: 82,
              title: "New Identity Movie",
              year: 2026,
              edition_label: null,
              poster_url: null,
              hidden_at: "2026-07-24T00:00:00+00:00",
            }],
          });
      }
      if (requestPath === "/api/cloud-libraries") {
        return Promise.resolve({ google: {}, my_libraries: [], shared_libraries: [] });
      }
      if (requestPath === "/api/auth/totp/status") {
        return Promise.resolve({ enabled: false, setup_available: false });
      }
      return Promise.resolve({});
    });
    const renderTree = () => (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={["/settings?section=libraries"]}>
          <SettingsPage />
        </MemoryRouter>
      </QueryClientProvider>
    );
    const view = testingLibraryRender(renderTree());
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith(
      "/api/user-hidden-items",
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ));

    mockAuthState.id = 8;
    view.rerender(renderTree());
    expect(await screen.findByText("New Identity Movie")).toBeInTheDocument();

    resolveFirstHiddenRequest({
      items: [{
        id: 81,
        title: "Old Identity Movie",
        year: 2026,
        edition_label: null,
        poster_url: null,
        hidden_at: "2026-07-23T00:00:00+00:00",
      }],
    });
    await Promise.resolve();
    await Promise.resolve();
    expect(screen.queryByText("Old Identity Movie")).not.toBeInTheDocument();
    expect(screen.getByText("New Identity Movie")).toBeInTheDocument();
  });
});

describe("SettingsPage Hidden scope transfer", () => {
  const personalHiddenItem = {
    id: 42,
    title: "Scope Movie",
    year: 2026,
    edition_label: "Extended",
    poster_url: null,
    hidden_at: "2026-07-23T00:00:00+00:00",
  };

  async function renderAdminLibraries(mockOptions = {}) {
    mockAuthState.role = "admin";
    mockApi(defaultSettings, mockOptions);
    render(
      <MemoryRouter initialEntries={["/settings?section=libraries"]}>
        <SettingsPage />
      </MemoryRouter>,
    );
    await screen.findByText("Hidden for me");
  }

  test("Hide universally sends one PUT and no compensating hide/show requests", async () => {
    await renderAdminLibraries({ hiddenItems: [personalHiddenItem] });
    fireEvent.click(screen.getByText("Hidden for me"));
    fireEvent.click(screen.getByRole("button", { name: "Hide universally" }));

    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith(
      "/api/admin/hidden-items/42/scope",
      {
        method: "PUT",
        data: { target_scope: "global" },
      },
    ));
    expect(apiRequest.mock.calls.filter(([path]) => path === "/api/admin/hidden-items/42/scope")).toHaveLength(1);
    expect(apiRequest).not.toHaveBeenCalledWith(
      "/api/admin/global-hidden-items/42",
      expect.objectContaining({ method: "POST" }),
    );
    expect(apiRequest).not.toHaveBeenCalledWith(
      "/api/user-hidden-items/42",
      expect.objectContaining({ method: "DELETE" }),
    );
    expect(await screen.findByText("This movie is hidden for everyone.")).toBeInTheDocument();
    expect(screen.getAllByText("Scope Movie")).toHaveLength(1);
  });

  test("Hide for me sends one PUT with the personal target", async () => {
    await renderAdminLibraries({ globalHiddenItems: [personalHiddenItem] });
    fireEvent.click(screen.getByText("Hidden for everyone"));
    fireEvent.click(screen.getByRole("button", { name: "Hide for me" }));

    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith(
      "/api/admin/hidden-items/42/scope",
      {
        method: "PUT",
        data: { target_scope: "personal" },
      },
    ));
    expect(apiRequest.mock.calls.filter(([path]) => path === "/api/admin/hidden-items/42/scope")).toHaveLength(1);
    expect(apiRequest).not.toHaveBeenCalledWith(
      "/api/user-hidden-items/42",
      expect.objectContaining({ method: "POST" }),
    );
    expect(apiRequest).not.toHaveBeenCalledWith(
      "/api/admin/global-hidden-items/42",
      expect.objectContaining({ method: "DELETE" }),
    );
    expect(await screen.findByText("This movie is now hidden only for your account.")).toBeInTheDocument();
  });

  test("scope transfer keeps the approved pending label and disabled state", async () => {
    await renderAdminLibraries({ hiddenItems: [personalHiddenItem] });
    fireEvent.click(screen.getByText("Hidden for me"));
    const originalImplementation = apiRequest.getMockImplementation();
    let resolveScopeRequest;
    apiRequest.mockImplementation((requestPath, options) => {
      if (requestPath === "/api/admin/hidden-items/42/scope") {
        return new Promise((resolve) => {
          resolveScopeRequest = resolve;
        });
      }
      return originalImplementation(requestPath, options);
    });

    fireEvent.click(screen.getByRole("button", { name: "Hide universally" }));

    const pendingButton = await screen.findByRole("button", { name: "Hiding globally..." });
    expect(pendingButton).toBeDisabled();
    resolveScopeRequest({
      item_id: 42,
      target_scope: "global",
      changed: true,
      hidden_at: "2026-07-24T03:04:05+00:00",
      message: "This movie is hidden for everyone.",
    });
    expect(await screen.findByText("This movie is hidden for everyone.")).toBeInTheDocument();
  });

  test("successful scope transfer uses the backend authoritative hidden timestamp", () => {
    const source = fs.readFileSync(settingsPagePath, "utf8");
    expect(source.match(/hidden_at:\s*payload\.hidden_at/g)).toHaveLength(4);
    expect(source).not.toContain("hidden_at: new Date().toISOString()");
  });

  test.each(["network", "malformed"])(
    "%s uncertainty reconciles authoritative lists and accepts a committed target",
    async (scopeError) => {
      await renderAdminLibraries({
        hiddenItems: [personalHiddenItem],
        scopeError,
      });
      const personalGetsBefore = apiRequest.mock.calls.filter(
        ([path]) => path === "/api/user-hidden-items",
      ).length;
      fireEvent.click(screen.getByText("Hidden for me"));
      fireEvent.click(screen.getByRole("button", { name: "Hide universally" }));

      expect(await screen.findByText("This movie is hidden for everyone.")).toBeInTheDocument();
      expect(apiRequest.mock.calls.filter(
        ([path]) => path === "/api/user-hidden-items",
      )).toHaveLength(personalGetsBefore + 1);
      expect(screen.queryByText("Elvern could not complete the request.")).not.toBeInTheDocument();
      expect(screen.queryByText("Elvern received an unreadable response from the server.")).not.toBeInTheDocument();
    },
  );

  test("explicit HTTP failure does not trigger reconciliation or fake success", async () => {
    await renderAdminLibraries({
      hiddenItems: [personalHiddenItem],
      scopeError: "http",
      scopeCommitsBeforeFailure: false,
    });
    const personalGetsBefore = apiRequest.mock.calls.filter(
      ([path]) => path === "/api/user-hidden-items",
    ).length;
    fireEvent.click(screen.getByText("Hidden for me"));
    fireEvent.click(screen.getByRole("button", { name: "Hide universally" }));

    expect(await screen.findByText("Explicit server failure")).toBeInTheDocument();
    expect(apiRequest.mock.calls.filter(
      ([path]) => path === "/api/user-hidden-items",
    )).toHaveLength(personalGetsBefore);
    expect(screen.queryByText("This movie is hidden for everyone.")).not.toBeInTheDocument();
  });

  test("uncertain result that did not commit keeps authoritative source and reports the error", async () => {
    await renderAdminLibraries({
      hiddenItems: [personalHiddenItem],
      scopeError: "network",
      scopeCommitsBeforeFailure: false,
    });
    fireEvent.click(screen.getByText("Hidden for me"));
    fireEvent.click(screen.getByRole("button", { name: "Hide universally" }));

    expect(await screen.findByText("Elvern could not complete the request.")).toBeInTheDocument();
    expect(screen.getByText("Scope Movie")).toBeInTheDocument();
    expect(screen.queryByText("This movie is hidden for everyone.")).not.toBeInTheDocument();
  });
});

describe("SettingsPage Display background controls", () => {
  test("display top row keeps poster controls beside the background card on wide layouts", () => {
    const source = fs.readFileSync(settingsPagePath, "utf8");
    const styles = fs.readFileSync(stylesPath, "utf8");

    expect(source).toContain("settings-card settings-display-card");
    expect(source).toContain("settings-card settings-background-card");
    expect(source).toContain("settings-card settings-display-interface-card");
    expect(source).toContain("settings-card settings-display-library-card");
    expect(source).toContain("settings-grid--compact-columns");
    expect(source).toContain("settings-grid__column");
    expect(source).toContain("{ value: \"clean\", label: \"Clean\" }");
    expect(source).not.toContain("settings-card settings-card--wide settings-display-card");
    expect(source).not.toContain("Customize your Elvern background for this account.");
    expect(source).not.toContain("Gradient start color");
    expect(source).not.toContain("Remove photo");
    expect(styles).toMatch(/\.settings-grid--display\s*\{[^}]*align-items:\s*start;/s);
    expect(styles).toMatch(/\.settings-grid__column\s*\{[^}]*align-content:\s*start;/s);
    expect(styles).not.toMatch(/\.settings-background-card\s*\{[^}]*grid-row:\s*1 \/ span 2;/s);
    expect(styles).not.toMatch(/\.settings-display-interface-card\s*\{[^}]*grid-row:\s*2;/s);
    expect(styles).toMatch(/data-elvern-background-preset="basic"\]\s*\{[^}]*#202832/s);
    expect(styles).toMatch(/\.settings-background-color-picker\s*\{[^}]*min-block-size:\s*18rem;/s);
    expect(styles).toContain("settings-segmented-control__indicator");
    expect(styles).toContain("settings-segmented-control__button--current");
    expect(source).toContain("settings-segmented-control--dragging");
    expect(source).toContain("const isCurrentLabel = dragging ? isPreviewSelected : isSelected;");
    expect(source).toContain("isCurrentLabel ? \"settings-segmented-control__button--current\" : \"\"");
    expect(styles).toMatch(/\.settings-segmented-control--dragging \.settings-segmented-control__button:not\(\.settings-segmented-control__button--active\)\s*\{[^}]*color:\s*var\(--text-muted\);/s);
    expect(styles).toContain("app-shell--poster-card-clean");
    expect(styles).toMatch(/\.app-shell--poster-card-clean[\s\S]*\.media-card__body[\s\S]*display:\s*none;/);
    expect(styles).toMatch(/\.detail-grid,\s*\.settings-grid\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\);/s);
    expect(styles).toMatch(/@media \(max-width:\s*640px\) and \(orientation:\s*portrait\)[\s\S]*--app-shell-inline-gutter:\s*clamp\(1\.15rem,\s*5\.5vw,\s*1\.55rem\);/);
    expect(styles).toMatch(/\.settings-card \.settings-segmented-control\s*\{[^}]*inline-size:\s*100%;/s);
    expect(styles).toMatch(/\.settings-card \.settings-segmented-control__button\s*\{[^}]*min-width:\s*0;/s);
    expect(styles).toMatch(/\.detail-info-modal__body\s*\{[^}]*scrollbar-width:\s*none;/s);
    expect(styles).toContain(".detail-info-modal__body::-webkit-scrollbar");
    expect(styles).not.toMatch(/\.detail-info-modal__body\s*\{[^}]*scrollbar-color:/s);
    expect(styles).toMatch(/\.settings-directory-picker__body\s*\{[^}]*scrollbar-width:\s*none;/s);
  });

  test("floating island drag is gated off for phone and tablet while settings segments stay draggable", () => {
    const settingsSource = fs.readFileSync(settingsPagePath, "utf8");
    const shellSource = fs.readFileSync(shellLayoutPath, "utf8");

    expect(settingsSource).toContain("handleActivePointerDown");
    expect(settingsSource).toContain("onPointerDown={isSelected ? handleActivePointerDown : undefined}");
    expect(shellSource).toContain("import { detectClientDeviceClass } from \"../lib/platformDetection\";");
    expect(shellSource).toContain("const floatingNavDragEnabled = clientDeviceClass !== \"phone\" && clientDeviceClass !== \"tablet\";");
    expect(shellSource).toContain("left: activeLinkRect.left - navRect.left + (navNode?.scrollLeft || 0),");
    expect(shellSource).toContain("navNode?.addEventListener(\"scroll\", updateFloatingNavIndicator, { passive: true });");
    expect(shellSource).toContain("const canDragCurrentItem = isCurrent && floatingNavDragEnabled;");
    expect(shellSource).toContain("onPointerDown={canDragCurrentItem ? handleFloatingActivePointerDown : undefined}");
  });

  test("interface search toggle uses the dynamic search button label", async () => {
    await renderDisplaySettings();

    expect(screen.getByText("Dynamic search button")).toBeInTheDocument();
    expect(screen.queryByText("Floating library search")).not.toBeInTheDocument();
  });

  test("phone hides the desktop-only dynamic search setting", async () => {
    mockPlatformState.deviceClass = "phone";
    await renderDisplaySettings();

    expect(screen.queryByText("Dynamic search button")).not.toBeInTheDocument();
  });

  test("interface settings no longer expose a floating island position control", async () => {
    await renderDisplaySettings();

    expect(screen.queryByText("Floating island position")).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Top" })).not.toBeInTheDocument();
    expect(screen.getByText("Dynamic search button")).toBeInTheDocument();
  });

  test("admin Libraries panel shows and manages age groups", async () => {
    mockAuthState.role = "admin";
    mockApi();
    render(
      <MemoryRouter initialEntries={["/settings?section=libraries"]}>
        <SettingsPage />
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: "Age Groups" });
    expect(screen.queryByText("Galaxy")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Review automatic movie age groups and explicit manual links."));

    expect(screen.queryByText("Unrestricted")).not.toBeInTheDocument();
    expect(screen.queryByText("None")).not.toBeInTheDocument();
    expect(screen.getByText("6")).toBeInTheDocument();
    expect(screen.getByText("18+")).toBeInTheDocument();
    expect(screen.getByText("3 copies")).toBeInTheDocument();
    expect(screen.getByText("2 copies · 1 manual link")).toBeInTheDocument();

    const adultBucket = screen.getByText("18+").closest("article");
    fireEvent.click(within(adultBucket).getByRole("button", { name: "Manage" }));

    await screen.findByRole("heading", { name: "Age 18+ groups" });
    expect(screen.getByText("Galaxy")).toBeInTheDocument();
    expect(screen.queryByText("Gentle Cartoon")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Remove age requirement" }));
    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith("/api/library/item/42/age-requirement", {
        method: "PATCH",
        data: { age_requirement: null },
      });
    });
    expect(await screen.findByText("No groups remain in this age bucket.")).toBeInTheDocument();
  });

  test("Age Groups manager opens individual group flow from a restricted bucket", async () => {
    mockAuthState.role = "admin";
    mockApi();
    render(
      <MemoryRouter initialEntries={["/settings?section=libraries"]}>
        <SettingsPage />
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: "Age Groups" });
    fireEvent.click(screen.getByText("Review automatic movie age groups and explicit manual links."));
    const adultBucket = screen.getByText("18+").closest("article");
    fireEvent.click(within(adultBucket).getByRole("button", { name: "Manage" }));
    await screen.findByRole("heading", { name: "Age 18+ groups" });
    fireEvent.click(screen.getByRole("button", { name: "Manage group" }));
    await screen.findByRole("heading", { name: "Age group" });
    expect(screen.getByText("Auto copies")).toBeInTheDocument();
    expect(screen.getByText("Manual copies")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("Search movie title"), { target: { value: "Galaxy" } });
    fireEvent.click(screen.getByRole("button", { name: "Search" }));

    await screen.findByText(/Auto group differs/);
    fireEvent.click(screen.getByRole("button", { name: "Link" }));

    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith("/api/library/age-groups/link", {
        method: "POST",
        data: {
          age_group_key: "age:title:galaxy|2026",
          target_media_item_id: 44,
        },
      });
    });
  });

  test("Age Groups panel shows the empty state when every group is unrestricted", async () => {
    mockAuthState.role = "admin";
    mockApi(defaultSettings, {
      ageGroupItems: [
        {
          age_group_key: "age:title:unrestricted|2023",
          display_title: "Unrestricted",
          year: 2023,
          age_requirement: null,
          age_requirement_display: "None",
          copies_count: 4,
          manual_links_count: 0,
          primary_media_item_id: 62,
        },
      ],
    });
    render(
      <MemoryRouter initialEntries={["/settings?section=libraries"]}>
        <SettingsPage />
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: "Age Groups" });
    fireEvent.click(screen.getByText("Review automatic movie age groups and explicit manual links."));
    expect(screen.getByText("No age-restricted movies yet.")).toBeInTheDocument();
    expect(screen.getByText("Set an age requirement from a movie's Info panel.")).toBeInTheDocument();
    expect(screen.queryByText("Unrestricted")).not.toBeInTheDocument();
  });

  test("Age Groups section is admin-only", async () => {
    mockApi();
    render(
      <MemoryRouter initialEntries={["/settings?section=libraries"]}>
        <SettingsPage />
      </MemoryRouter>,
    );

    await screen.findByRole("heading", { name: "Library" });
    expect(screen.queryByRole("heading", { name: "Age Groups" })).not.toBeInTheDocument();
  });

  test("admin Advanced panel shows Library reference locations summary", async () => {
    mockAuthState.role = "admin";
    mockApi();
    render(
      <MemoryRouter initialEntries={["/settings?section=advanced"]}>
        <SettingsPage />
      </MemoryRouter>,
    );

    await screen.findByText("Library reference locations");
    fireEvent.click(screen.getByText("Library reference locations"));

    expect(screen.getByLabelText("Reference locations")).toHaveValue("/srv/media");
    expect(screen.getByText("Movies stored under:")).toBeInTheDocument();
    expect(screen.getByText("/srv/media/Movies -M")).toBeInTheDocument();
    expect(screen.getByText("TV stored under:")).toBeInTheDocument();
    expect(screen.getAllByText("Unknown").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Cartoon stored under:")).toBeInTheDocument();
    expect(screen.getByText("/srv/media/Cartoons -C")).toBeInTheDocument();
    expect(screen.getByText("Anime stored under:")).toBeInTheDocument();
    expect(screen.queryByText("Shared local library path")).not.toBeInTheDocument();
  });

  test("library reference folder button sends native picker library purpose", async () => {
    mockAuthState.role = "admin";
    mockApi();
    render(
      <MemoryRouter initialEntries={["/settings?section=advanced"]}>
        <SettingsPage />
      </MemoryRouter>,
    );

    await screen.findByText("Library reference locations");
    fireEvent.click(screen.getByText("Library reference locations"));
    fireEvent.click(screen.getByRole("button", { name: "Browse library reference directories on the Elvern host" }));

    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith("/api/admin/local-directory-picker", {
        method: "POST",
        data: expect.objectContaining({
          purpose: "library_reference",
          platform: "linux",
        }),
      });
    });
    const pickerCall = apiRequest.mock.calls.find(([requestPath]) => requestPath === "/api/admin/local-directory-picker");
    expect(pickerCall?.[1]?.data).not.toHaveProperty("title");
    expect(await screen.findByLabelText("Reference locations")).toHaveValue("/srv/media/selected-library");
  });

  test("poster reference folder button sends native picker poster purpose", async () => {
    mockAuthState.role = "admin";
    mockApi();
    render(
      <MemoryRouter initialEntries={["/settings?section=advanced"]}>
        <SettingsPage />
      </MemoryRouter>,
    );

    await screen.findByText("Poster reference location");
    fireEvent.click(screen.getByText("Poster reference location"));
    fireEvent.click(screen.getByRole("button", { name: "Browse poster directories on the Elvern host" }));

    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith("/api/admin/local-directory-picker", {
        method: "POST",
        data: expect.objectContaining({
          purpose: "poster_reference",
          platform: "linux",
        }),
      });
    });
    const pickerCall = apiRequest.mock.calls.find(([requestPath]) => requestPath === "/api/admin/local-directory-picker");
    expect(pickerCall?.[1]?.data).not.toHaveProperty("title");
    expect(await screen.findByLabelText("Poster directory")).toHaveValue("/srv/posters/selected");
  });

  test("poster appearance controls still save through the existing settings endpoint", async () => {
    await renderDisplaySettings();

    fireEvent.click(screen.getByRole("radio", { name: "Modern" }));

    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith("/api/user-settings", {
        method: "PATCH",
        data: { poster_card_appearance: "modern" },
      });
    });

    fireEvent.click(screen.getByRole("radio", { name: "Clean" }));

    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith("/api/user-settings", {
        method: "PATCH",
        data: { poster_card_appearance: "clean" },
      });
    });
  });

  test("poster width PATCH updates the shared user settings cache immediately", async () => {
    await renderDisplaySettings();

    fireEvent.change(screen.getByRole("combobox", { name: /Poster display quality/i }), {
      target: { value: "800" },
    });

    await waitFor(() => expect(queryClient.getQueryData(buildUserSettingsQueryKey({
      userId: 7,
      role: "standard_user",
    }))).toMatchObject({ poster_card_display_max_width: "800" }));
    expect(apiRequest).toHaveBeenCalledWith("/api/user-settings", {
      method: "PATCH",
      data: { poster_card_display_max_width: "800" },
    });
  });

  test("background presets render with Neon selected by default and Basic saves as a preset", async () => {
    await renderDisplaySettings();

    expect(screen.getByRole("radio", { name: "Neon" })).toHaveAttribute("aria-checked", "true");
    expect(screen.queryByLabelText("Background preview")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: "Basic" }));

    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith("/api/user-settings", {
        method: "PATCH",
        data: {
          background_mode: "preset",
          background_preset: "basic",
        },
      });
    });
  });

  test("gradient and solid use one palette picker and save only through their save buttons", async () => {
    await renderDisplaySettings();

    fireEvent.click(screen.getByRole("radio", { name: "Gradient" }));

    expect(screen.getByRole("slider", { name: "Gradient background color picker" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Gradient start color")).not.toBeInTheDocument();
    expect(screen.queryByText("Start")).not.toBeInTheDocument();
    expect(apiRequest).not.toHaveBeenCalledWith("/api/user-settings", {
      method: "PATCH",
      data: { background_mode: "gradient" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Save gradient" }));

    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith("/api/user-settings", {
        method: "PATCH",
        data: expect.objectContaining({ background_mode: "gradient" }),
      });
    });

    fireEvent.click(screen.getByRole("radio", { name: "Solid" }));

    expect(screen.getByRole("slider", { name: "Solid background color picker" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Solid background color")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save solid" })).toBeInTheDocument();
  });

  test("background photo tab opens before upload and reset uses an in-app confirmation", async () => {
    await renderDisplaySettings();

    fireEvent.click(screen.getByRole("radio", { name: "Photo" }));

    expect(screen.getByText("Upload photo")).toBeInTheDocument();
    expect(apiRequest).not.toHaveBeenCalledWith("/api/user-settings", {
      method: "PATCH",
      data: { background_mode: "photo" },
    });
  });

  test("photo upload rejects unsupported types with styled page feedback", async () => {
    await renderDisplaySettings();
    fireEvent.click(screen.getByRole("radio", { name: "Photo" }));
    const fileInput = document.querySelector("input[type='file']");

    fireEvent.change(fileInput, {
      target: {
        files: [new File(["<svg />"], "bad.svg", { type: "image/svg+xml" })],
      },
    });

    expect(await screen.findByText("Choose a JPEG, PNG, or WebP image.")).toHaveClass("form-error");
    expect(apiRequest).not.toHaveBeenCalledWith(
      "/api/user-settings/background-photo",
      expect.objectContaining({ method: "POST" }),
    );
  });

  test("photo upload and reset return the background state through the settings event path", async () => {
    await renderDisplaySettings();
    fireEvent.click(screen.getByRole("radio", { name: "Photo" }));
    const fileInput = document.querySelector("input[type='file']");

    fireEvent.change(fileInput, {
      target: {
        files: [new File(["not-real-but-backend-is-mocked"], "bg.png", { type: "image/png" })],
      },
    });

    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith(
        "/api/user-settings/background-photo",
        expect.objectContaining({ method: "POST" }),
      );
    });

    fireEvent.click(await screen.findByRole("button", { name: "Reset" }));

    const dialog = await screen.findByRole("dialog", { name: "Reset background?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Reset" }));

    await waitFor(() => {
      expect(apiRequest).toHaveBeenCalledWith("/api/user-settings/background-photo", {
        method: "DELETE",
      });
    });
  });

  test("settings page does not use browser popups for background controls", () => {
    const source = fs.readFileSync(settingsPagePath, "utf8");
    expect(source).not.toMatch(/window\.(?:alert|confirm|prompt)\b/);
    expect(source).not.toMatch(/\b(?:alert|confirm|prompt)\s*\(/);
  });
});

describe("background style helpers", () => {
  test("gradient uses the required top-left to bottom-right direction", () => {
    const style = buildBackgroundPreviewStyle({
      background_mode: "gradient",
      background_gradient_start: "#112233",
      background_gradient_accent: "#445566",
      background_gradient_end: "#778899",
    });

    expect(style.background).toContain("linear-gradient(135deg");
  });

  test("single gradient color derives a real second color", () => {
    expect(deriveGradientEndFromSingleColor("#336699")).not.toBe("#336699");
  });

  test("solid mode is the only flat custom background mode", () => {
    const solidStyle = buildBackgroundPreviewStyle({
      background_mode: "solid",
      background_solid_color: "#223344",
    });
    const gradientStyle = buildBackgroundPreviewStyle({
      background_mode: "gradient",
      background_gradient_start: "#223344",
      background_gradient_end: "#223344",
      background_gradient_accent: "#334455",
    });

    expect(solidStyle.background).toBe("#223344");
    expect(gradientStyle.background).toContain("linear-gradient(135deg");
  });
});
