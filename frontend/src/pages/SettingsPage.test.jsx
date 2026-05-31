import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { apiRequest } from "../lib/api";
import {
  buildBackgroundPreviewStyle,
  deriveGradientEndFromSingleColor,
} from "../lib/userBackground";
import { SettingsPage } from "./SettingsPage";

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    user: {
      id: 7,
      username: "display-user",
      role: "standard_user",
    },
  }),
}));

vi.mock("../lib/api", () => ({
  apiRequest: vi.fn(),
}));

const pagesDir = path.dirname(fileURLToPath(import.meta.url));
const settingsPagePath = path.resolve(pagesDir, "SettingsPage.jsx");
const stylesPath = path.resolve(pagesDir, "../styles.css");

const defaultSettings = {
  hide_duplicate_movies: true,
  hide_recently_added: false,
  floating_controls_position: "bottom",
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
};

function mockApi(initialSettings = defaultSettings) {
  let settings = { ...initialSettings };
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
      return Promise.resolve({ items: [] });
    }
    if (requestPath === "/api/cloud-libraries") {
      return Promise.resolve({
        google: { enabled: false, connected: false },
        my_libraries: [],
        shared_libraries: [],
      });
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
  apiRequest.mockReset();
});

describe("SettingsPage Display background controls", () => {
  test("display top row keeps poster controls beside the background card on wide layouts", () => {
    const source = fs.readFileSync(settingsPagePath, "utf8");
    const styles = fs.readFileSync(stylesPath, "utf8");

    expect(source).toContain("settings-card settings-display-card");
    expect(source).toContain("settings-card settings-background-card");
    expect(source).toContain("settings-card settings-display-interface-card");
    expect(source).toContain("settings-card settings-display-library-card");
    expect(source).not.toContain("settings-card settings-card--wide settings-display-card");
    expect(source).not.toContain("Customize your Elvern background for this account.");
    expect(styles).toMatch(/\.settings-grid--display\s*\{[^}]*align-items:\s*start;/s);
    expect(styles).toMatch(/\.settings-background-card\s*\{[^}]*grid-row:\s*1 \/ span 2;/s);
    expect(styles).toMatch(/\.settings-display-interface-card\s*\{[^}]*grid-row:\s*2;/s);
    expect(styles).toMatch(/\.detail-grid,\s*\.settings-grid\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\);/s);
  });

  test("interface search toggle uses the dynamic search button label", async () => {
    await renderDisplaySettings();

    expect(screen.getByText("Dynamic search button")).toBeInTheDocument();
    expect(screen.queryByText("Floating library search")).not.toBeInTheDocument();
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
  });

  test("background presets render with Neon selected by default and Basic saves as a preset", async () => {
    await renderDisplaySettings();

    expect(screen.getByRole("radio", { name: "Neon" })).toHaveAttribute("aria-checked", "true");
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

  test("photo upload rejects unsupported types with styled page feedback", async () => {
    await renderDisplaySettings();
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

  test("photo upload and remove return the background state through the settings event path", async () => {
    await renderDisplaySettings();
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

    fireEvent.click(await screen.findByRole("button", { name: "Remove photo" }));

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
