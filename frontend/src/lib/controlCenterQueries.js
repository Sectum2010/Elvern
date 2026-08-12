import { apiRequest } from "./api.js";
import { queryClient } from "./queryClient.js";
import { createExternalNavigationAwareRequestOwner } from "./externalNavigationCoordinator.js";

export const CONTROL_CENTER_QUERY_PREFIX = Object.freeze(["control-center", "v1"]);
export const CONTROL_CENTER_RESOURCE_STALE_TIME_MS = 30_000;
export const CONTROL_CENTER_RESOURCE_GC_TIME_MS = 4 * 60 * 60 * 1000;
export const CONTROL_CENTER_RECOVERY_MAX_CONCURRENCY = 2;

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
  mediaReference: "/api/admin/media-library-reference",
  posterReference: "/api/admin/poster-reference-location",
  ageGroups: "/api/library/age-groups",
  hiddenTitles: "/api/settings/hidden-titles",
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
  const identity = `${String(userId ?? "").trim()}:${normalize(role)}`;
  return queryClient.fetchQuery({
    queryKey,
    queryFn: ({ signal }) => {
      const requestOwner = createExternalNavigationAwareRequestOwner({ identity, resource });
      return apiRequest(
        controlCenterResourcePath({ resource, platform, deviceId }),
        { signal, requestOwner, abortOnPageHide: true },
      );
    },
    staleTime: force ? 0 : CONTROL_CENTER_RESOURCE_STALE_TIME_MS,
    gcTime: CONTROL_CENTER_RESOURCE_GC_TIME_MS,
    retry: false,
  });
}


export async function runControlCenterRecoveryTasks(tasks, {
  maxConcurrency = CONTROL_CENTER_RECOVERY_MAX_CONCURRENCY,
} = {}) {
  const queue = Array.isArray(tasks) ? tasks.filter((task) => typeof task === "function") : [];
  const workerCount = Math.min(Math.max(1, Number(maxConcurrency) || 1), queue.length);
  let nextIndex = 0;
  async function worker() {
    while (nextIndex < queue.length) {
      const taskIndex = nextIndex;
      nextIndex += 1;
      await queue[taskIndex]();
    }
  }
  await Promise.all(Array.from({ length: workerCount }, worker));
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
  return queryClient.getQueryData(
    buildControlCenterResourceQueryKey({ userId, role, resource, platform, deviceId }),
  );
}

export function getControlCenterResourceData({
  userId,
  role,
  resource,
  platform = "",
  deviceId = "",
} = {}) {
  return queryClient.getQueryData(
    buildControlCenterResourceQueryKey({ userId, role, resource, platform, deviceId }),
  );
}

export function invalidateControlCenterResource({
  userId,
  role,
  resource,
  platform = "",
  deviceId = "",
} = {}) {
  return queryClient.invalidateQueries({
    queryKey: buildControlCenterResourceQueryKey({ userId, role, resource, platform, deviceId }),
    exact: true,
  });
}
