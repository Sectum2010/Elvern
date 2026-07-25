export const SETTINGS_ACTIVE_SECTION_STORAGE_KEY = "elvern:settings-active-section";
export const SETTINGS_SECTION_KEYS = Object.freeze([
  "preferences",
  "display",
  "libraries",
  "install",
  "advanced",
]);

const SETTINGS_SECTION_ALIASES = Object.freeze({
  hidden: "libraries",
});


export function canonicalizeSettingsSection(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (SETTINGS_SECTION_KEYS.includes(normalized)) {
    return normalized;
  }
  return SETTINGS_SECTION_ALIASES[normalized] || null;
}


function defaultStorage() {
  if (typeof window === "undefined") {
    return null;
  }
  return window.localStorage;
}


export function writePersistedSettingsSection(section, storage = defaultStorage()) {
  if (!storage || !SETTINGS_SECTION_KEYS.includes(section)) {
    return false;
  }
  try {
    storage.setItem(SETTINGS_ACTIVE_SECTION_STORAGE_KEY, section);
    return true;
  } catch {
    return false;
  }
}


export function readPersistedSettingsSection(storage = defaultStorage()) {
  if (!storage) {
    return "preferences";
  }
  try {
    const storedValue = storage.getItem(SETTINGS_ACTIVE_SECTION_STORAGE_KEY);
    const canonical = canonicalizeSettingsSection(storedValue);
    if (!canonical) {
      return "preferences";
    }
    if (canonical !== storedValue) {
      writePersistedSettingsSection(canonical, storage);
    }
    return canonical;
  } catch {
    return "preferences";
  }
}


export function resolveSettingsSection({ search = "", storage = defaultStorage() } = {}) {
  const params = new URLSearchParams(search);
  if (!params.has("section")) {
    return {
      section: readPersistedSettingsSection(storage),
      shouldReplace: false,
    };
  }

  const rawSection = params.get("section");
  const canonical = canonicalizeSettingsSection(rawSection);
  if (canonical) {
    return {
      section: canonical,
      shouldReplace: canonical !== rawSection,
    };
  }
  return {
    section: readPersistedSettingsSection(storage),
    shouldReplace: true,
  };
}


export function buildSettingsSectionLocation(location, section) {
  const canonical = canonicalizeSettingsSection(section) || "preferences";
  const params = new URLSearchParams(location?.search || "");
  params.set("section", canonical);
  const search = params.toString();
  return `${location?.pathname || "/settings"}?${search}${location?.hash || ""}`;
}
