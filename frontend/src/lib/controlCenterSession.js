export const CONTROL_CENTER_THEME_KEY = "elvern:control-center:theme";
export const CONTROL_CENTER_SETTINGS_TAB_KEY = "elvern:control-center:settings-tab";
export const CONTROL_CENTER_ADMIN_TAB_KEY = "elvern:control-center:admin-tab";
export const CONTROL_CENTER_SESSION_RESET_EVENT = "elvern:control-center-session-reset";

export const CONTROL_CENTER_THEMES = Object.freeze(["light", "mixed", "dark"]);
export const CONTROL_CENTER_SETTINGS_TABS = Object.freeze([
  "appearance",
  "library",
  "cloud-sharing",
  "hidden-titles",
  "playback-apps",
  "server-storage",
]);
export const CONTROL_CENTER_ADMIN_TABS = Object.freeze([
  "overview",
  "users-invites",
  "security",
  "logs",
  "recovery",
]);

function getSessionStorage() {
  return typeof window === "undefined" ? null : window.sessionStorage;
}

function readAllowed(key, allowed, fallback, storage = getSessionStorage()) {
  try {
    const value = storage?.getItem?.(key) || "";
    return allowed.includes(value) ? value : fallback;
  } catch {
    return fallback;
  }
}

function writeAllowed(key, value, allowed, storage = getSessionStorage()) {
  if (!allowed.includes(value)) {
    return false;
  }
  try {
    storage?.setItem?.(key, value);
    return true;
  } catch {
    return false;
  }
}

export function readControlCenterTheme(storage) {
  return readAllowed(CONTROL_CENTER_THEME_KEY, CONTROL_CENTER_THEMES, "light", storage);
}

export function writeControlCenterTheme(theme, storage) {
  return writeAllowed(CONTROL_CENTER_THEME_KEY, theme, CONTROL_CENTER_THEMES, storage);
}

export function readControlCenterTab(area, storage) {
  if (area === "admin") {
    return readAllowed(CONTROL_CENTER_ADMIN_TAB_KEY, CONTROL_CENTER_ADMIN_TABS, "overview", storage);
  }
  return readAllowed(CONTROL_CENTER_SETTINGS_TAB_KEY, CONTROL_CENTER_SETTINGS_TABS, "appearance", storage);
}

export function writeControlCenterTab(area, tab, storage) {
  if (area === "admin") {
    return writeAllowed(CONTROL_CENTER_ADMIN_TAB_KEY, tab, CONTROL_CENTER_ADMIN_TABS, storage);
  }
  return writeAllowed(CONTROL_CENTER_SETTINGS_TAB_KEY, tab, CONTROL_CENTER_SETTINGS_TABS, storage);
}

export function clearControlCenterSessionState(storage = getSessionStorage()) {
  try {
    storage?.removeItem?.(CONTROL_CENTER_THEME_KEY);
    storage?.removeItem?.(CONTROL_CENTER_SETTINGS_TAB_KEY);
    storage?.removeItem?.(CONTROL_CENTER_ADMIN_TAB_KEY);
  } catch {
    // Session UI state is best-effort and never blocks authentication.
  }
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(CONTROL_CENTER_SESSION_RESET_EVENT));
  }
}
