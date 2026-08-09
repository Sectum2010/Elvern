import { apiRequest } from "./api.js";
import { queryClient } from "./queryClient.js";

export const CONTROL_CENTER_QUERY_PREFIX = Object.freeze(["control-center", "v1"]);
export const CONTROL_CENTER_RESOURCE_STALE_TIME_MS = 30_000;
export const CONTROL_CENTER_RESOURCE_GC_TIME_MS = 4 * 60 * 60 * 1000;

const STATIC_RESOURCE_PATHS = Object.freeze({
  system: "/api/system/status",
  users: "/api/admin/users",
  sessions: "/api/admin/sessions",
  audit: "/api/admin/audit?limit=100",
  urlPrefix: "/api/admin/url-prefix",
  ownTotp: "/api/auth/totp/status",
  invites: "/api/admin/invite-codes",
  passwordHelp: "/api/admin/password-help-requests",
  exposure: "/api/admin/exposure/status",
  backups: "/api/admin/backups",
  cloudLibraries: "/api/cloud-libraries",
  googleDriveSetup: "/api/admin/google-drive-setup",
  personalHidden: "/api/user-hidden-items",
  globalHidden: "/api/admin/global-hidden-items",
  userSettings: "/api/user-settings",
});

function normalize(value) {
  return String(value ?? "").trim().toLowerCase();
}

export function buildControlCenterResourceQueryKey({
  userId,
  role,
  resource,
  platform = "",
  deviceId = "",
} = {}) {
  return [
    ...CONTROL_CENTER_QUERY_PREFIX,
    {
      userId: String(userId ?? "").trim(),
      role: normalize(role),
      resource: normalize(resource),
      platform: normalize(platform),
      deviceId: String(deviceId ?? "").trim(),
    },
  ];
}

export function controlCenterResourcePath({ resource, platform = "", deviceId = "" }) {
  if (resource === "desktopHelper") {
    const params = new URLSearchParams({ platform: normalize(platform) });
    if (deviceId) {
      params.set("device_id", String(deviceId));
    }
    return `/api/desktop-helper/status?${params.toString()}`;
  }
  const path = STATIC_RESOURCE_PATHS[resource];
  if (!path) {
    throw new Error(`Unknown Control Center resource: ${resource}`);
  }
  return path;
}

export function fetchControlCenterResource({
  userId,
  role,
  resource,
  platform = "",
  deviceId = "",
  force = false,
} = {}) {
  const queryKey = buildControlCenterResourceQueryKey({ userId, role, resource, platform, deviceId });
  return queryClient.fetchQuery({
    queryKey,
    queryFn: ({ signal }) => apiRequest(
      controlCenterResourcePath({ resource, platform, deviceId }),
      { signal, abortOnPageHide: true },
    ),
    staleTime: force ? 0 : CONTROL_CENTER_RESOURCE_STALE_TIME_MS,
    gcTime: CONTROL_CENTER_RESOURCE_GC_TIME_MS,
    retry: false,
  });
}

export function setControlCenterResourceData({
  userId,
  role,
  resource,
  platform = "",
  deviceId = "",
}, payload) {
  queryClient.setQueryData(
    buildControlCenterResourceQueryKey({ userId, role, resource, platform, deviceId }),
    payload,
  );
  return payload;
}
