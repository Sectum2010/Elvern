import {
  CONTROL_CENTER_ADMIN_TABS,
  CONTROL_CENTER_SETTINGS_TABS,
  readControlCenterTab,
} from "./controlCenterSession.js";

const DESKTOP_PLATFORMS = new Set(["windows", "mac", "linux"]);

const SETTINGS_LEGACY_SECTIONS = Object.freeze({
  preferences: "appearance",
  display: "appearance",
  libraries: "cloud-sharing",
  hidden: "hidden-titles",
  install: "playback-apps",
});

const ADMIN_LEGACY_SECTIONS = Object.freeze({
  panel: "users-invites",
  security: "security",
  logs: "logs",
  recovery: "recovery",
});

export function isDesktopControlCenterDevice(deviceClass, platform) {
  return deviceClass === "desktop" && DESKTOP_PLATFORMS.has(platform);
}

export function classifyControlCenterPath(pathname) {
  const path = String(pathname || "");
  const settingsMatch = path.match(/^\/settings(?:\/(.*))?$/);
  if (settingsMatch) {
    const tab = settingsMatch[1] || "";
    return {
      area: "settings",
      tab: CONTROL_CENTER_SETTINGS_TABS.includes(tab) ? tab : "",
      valid: !tab || CONTROL_CENTER_SETTINGS_TABS.includes(tab),
    };
  }
  if (/^\/admin\/assistant(?:\/|$)/.test(path)) {
    return { area: "", tab: "", valid: false };
  }
  const adminMatch = path.match(/^\/admin(?:\/(.*))?$/);
  if (adminMatch) {
    const tab = adminMatch[1] || "";
    return {
      area: "admin",
      tab: CONTROL_CENTER_ADMIN_TABS.includes(tab) ? tab : "",
      valid: !tab || CONTROL_CENTER_ADMIN_TABS.includes(tab),
    };
  }
  return { area: "", tab: "", valid: false };
}

export function desktopSettingsTabToLegacySection(tab, role = "standard_user") {
  if (tab === "library" || tab === "cloud-sharing" || tab === "hidden-titles") {
    return "libraries";
  }
  if (tab === "playback-apps") {
    return "install";
  }
  if (tab === "server-storage") {
    return role === "admin" ? "advanced" : "preferences";
  }
  return "preferences";
}

export function desktopAdminTabToLegacySection(tab) {
  if (tab === "users-invites") {
    return "panel";
  }
  if (tab === "logs" || tab === "recovery") {
    return tab;
  }
  return "security";
}

function buildLocation(pathname, params, hash) {
  const search = params.toString();
  return `${pathname}${search ? `?${search}` : ""}${hash || ""}`;
}

export function resolveControlCenterLocation({
  pathname = "",
  search = "",
  hash = "",
  role = "standard_user",
  storage,
} = {}) {
  const classification = classifyControlCenterPath(pathname);
  if (!classification.area) {
    return null;
  }
  const params = new URLSearchParams(search);
  const rawLegacySection = String(params.get("section") || "").trim().toLowerCase();
  let targetTab = classification.tab;

  if (classification.area === "settings") {
    if (!classification.valid) {
      targetTab = "appearance";
    } else if (!targetTab) {
      if (rawLegacySection === "libraries" && hash === "#hidden-list") {
        targetTab = "hidden-titles";
      } else if (hash === "#google-drive-oauth-setup") {
        targetTab = "server-storage";
      } else if (params.has("googleDriveStatus")) {
        targetTab = "cloud-sharing";
      } else if (rawLegacySection === "advanced") {
        targetTab = role === "admin" ? "server-storage" : "appearance";
      } else {
        targetTab = SETTINGS_LEGACY_SECTIONS[rawLegacySection]
          || readControlCenterTab("settings", storage);
      }
    }
    params.delete("section");
    const target = buildLocation(`/settings/${targetTab}`, params, hash);
    return target === `${pathname}${search}${hash}` ? null : target;
  }

  if (!classification.valid) {
    targetTab = "overview";
  } else if (!targetTab) {
    targetTab = ADMIN_LEGACY_SECTIONS[rawLegacySection]
      || readControlCenterTab("admin", storage);
  }
  params.delete("section");
  const target = buildLocation(`/admin/${targetTab}`, params, hash);
  return target === `${pathname}${search}${hash}` ? null : target;
}
