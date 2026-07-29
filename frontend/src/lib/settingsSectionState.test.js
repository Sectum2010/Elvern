import { describe, expect, test, vi } from "vitest";

import {
  SETTINGS_ACTIVE_SECTION_STORAGE_KEY,
  SETTINGS_SECTION_KEYS,
  applySettingsSectionStorageMigration,
  buildSettingsSectionLocation,
  canonicalizeSettingsSection,
  readPersistedSettingsSection,
  resolveSettingsSection,
  writePersistedSettingsSection,
} from "./settingsSectionState.js";


describe("Settings section state", () => {
  test.each(SETTINGS_SECTION_KEYS)("keeps the canonical %s section", (section) => {
    expect(canonicalizeSettingsSection(section)).toBe(section);
  });

  test("maps the legacy hidden section to libraries", () => {
    expect(canonicalizeSettingsSection("hidden")).toBe("libraries");
  });

  test("reads a legacy hidden storage value without writing during resolution", () => {
    const storage = {
      getItem: vi.fn(() => "hidden"),
      setItem: vi.fn(),
    };

    expect(readPersistedSettingsSection(storage)).toBe("libraries");
    expect(storage.setItem).not.toHaveBeenCalled();
    const resolution = resolveSettingsSection({ storage });
    expect(resolution).toMatchObject({
      section: "libraries",
      needsStorageMigration: true,
      storageMigrationTarget: "libraries",
    });
    expect(storage.setItem).not.toHaveBeenCalled();
    expect(applySettingsSectionStorageMigration(resolution, storage)).toBe(true);
    expect(storage.setItem).toHaveBeenCalledWith(SETTINGS_ACTIVE_SECTION_STORAGE_KEY, "libraries");
  });

  test("uses a valid URL section before storage", () => {
    const storage = {
      getItem: vi.fn(() => "advanced"),
      setItem: vi.fn(),
    };

    expect(resolveSettingsSection({
      search: "?section=display",
      storage,
    })).toMatchObject({
      section: "display",
      shouldReplace: false,
    });
  });

  test("uses storage only when the URL section is missing", () => {
    const storage = {
      getItem: vi.fn(() => "install"),
      setItem: vi.fn(),
    };

    expect(resolveSettingsSection({ search: "?other=1", storage })).toMatchObject({
      section: "install",
      shouldReplace: false,
    });
  });

  test("replaces invalid and legacy URL sections with a canonical fallback", () => {
    const storage = {
      getItem: vi.fn(() => "advanced"),
      setItem: vi.fn(),
    };

    expect(resolveSettingsSection({ search: "?section=garbage", storage })).toMatchObject({
      section: "advanced",
      shouldReplace: true,
    });
    expect(resolveSettingsSection({ search: "?section=hidden", storage })).toMatchObject({
      section: "libraries",
      shouldReplace: true,
    });
  });

  test("removes invalid storage only when migration is applied", () => {
    const storage = {
      getItem: vi.fn(() => "garbage"),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    };
    const resolution = resolveSettingsSection({ storage });

    expect(resolution).toMatchObject({
      section: "preferences",
      needsStorageMigration: true,
      storageMigrationTarget: null,
    });
    expect(storage.removeItem).not.toHaveBeenCalled();
    applySettingsSectionStorageMigration(resolution, storage);
    expect(storage.removeItem).toHaveBeenCalledWith(SETTINGS_ACTIVE_SECTION_STORAGE_KEY);
  });

  test("builds a replacement that preserves other query parameters and hash", () => {
    expect(buildSettingsSectionLocation({
      pathname: "/settings",
      search: "?googleDriveStatus=connected&section=hidden&other=1",
      hash: "#oauth",
    }, "libraries")).toBe(
      "/settings?googleDriveStatus=connected&section=libraries&other=1#oauth",
    );
  });

  test("never writes a legacy or invalid section", () => {
    const storage = { setItem: vi.fn() };

    expect(writePersistedSettingsSection("hidden", storage)).toBe(false);
    expect(writePersistedSettingsSection("garbage", storage)).toBe(false);
    expect(storage.setItem).not.toHaveBeenCalled();
  });
});
