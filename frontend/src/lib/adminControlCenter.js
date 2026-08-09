export const ADMIN_LIVE_AUDIT_TICKER_LINE = [
  "admin · auth.login · Jul 30, 4:25 PM",
  "admin · auth.login · Jul 30, 4:20 PM",
  "helen · auth.logout · Jul 30, 3:54 PM",
  "helen · auth.login · Jul 30, 3:53 PM",
  "admin · auth.logout · Jul 30, 12:38 AM",
  "admin · admin.library.rescan · Jul 30, 12:10 AM",
  "admin · auth.login · Jul 29, 9:34 PM",
  "caleb · auth.login · Jul 29, 12:06 PM",
].join("      ●      ");

const DESKTOP_ADMIN_RESOURCES = Object.freeze({
  overview: ["system", "users", "sessions", "exposure"],
  "users-invites": ["system", "users", "invites", "passwordHelp"],
  security: ["system", "users", "sessions", "invites", "urlPrefix", "ownTotp"],
  logs: ["system", "sessions", "audit"],
  recovery: ["system", "audit", "backups"],
});

export function desktopAdminResourcesForTab(tab) {
  return [...(DESKTOP_ADMIN_RESOURCES[tab] || DESKTOP_ADMIN_RESOURCES.overview)];
}

export function shouldPollDesktopPlaybackWorkers(tab, visibilityState = "visible") {
  return tab === "users-invites" && visibilityState === "visible";
}

export function shouldRefreshDesktopRealtimeResource(tab, resource) {
  if (resource === "users") {
    return tab === "overview" || tab === "users-invites" || tab === "security";
  }
  if (resource === "sessions") {
    return tab === "overview" || tab === "security" || tab === "logs";
  }
  return false;
}
