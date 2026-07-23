import { useQuery } from "@tanstack/react-query";

import { apiRequest } from "./api";
import { useBoundedQueryRecovery } from "./boundedQueryRecovery";
import { queryClient } from "./queryClient";
import { DEFAULT_BACKGROUND_SETTINGS } from "./userBackground";


export const USER_SETTINGS_QUERY_STALE_TIME_MS = 5 * 60 * 1000;
export const USER_SETTINGS_QUERY_GC_TIME_MS = 4 * 60 * 60 * 1000;
export const USER_SETTINGS_QUERY_PREFIX = Object.freeze(["user-settings", "v1"]);
export const DEFAULT_USER_SETTINGS = Object.freeze({
  hide_duplicate_movies: true,
  hide_recently_added: false,
  floating_library_search_enabled: true,
  poster_card_appearance: "classic",
  poster_card_display_max_width: "1400",
  ...DEFAULT_BACKGROUND_SETTINGS,
});


function normalizeString(value) {
  return String(value ?? "").trim();
}


export function normalizeUserSettingsQueryIdentity({ userId, role } = {}) {
  return {
    userId: normalizeString(userId),
    role: normalizeString(role).toLowerCase(),
  };
}


export function buildUserSettingsQueryKey(identity = {}) {
  return [
    ...USER_SETTINGS_QUERY_PREFIX,
    normalizeUserSettingsQueryIdentity(identity),
  ];
}


export function isUserSettingsQueryKey(queryKey) {
  return Array.isArray(queryKey)
    && queryKey[0] === USER_SETTINGS_QUERY_PREFIX[0]
    && queryKey[1] === USER_SETTINGS_QUERY_PREFIX[1];
}


export function resolveUserSettings(payload) {
  return {
    ...DEFAULT_USER_SETTINGS,
    ...(payload && typeof payload === "object" ? payload : {}),
  };
}


export function useUserSettingsQuery(user) {
  const query = useQuery({
    queryKey: buildUserSettingsQueryKey({ userId: user?.id, role: user?.role }),
    queryFn: ({ signal }) => apiRequest("/api/user-settings", {
      signal,
      abortOnPageHide: true,
    }),
    enabled: Boolean(user?.id),
    staleTime: USER_SETTINGS_QUERY_STALE_TIME_MS,
    gcTime: USER_SETTINGS_QUERY_GC_TIME_MS,
    retry: false,
  });
  // A transient transport failure keeps cached settings (or defaults) usable and
  // recovers once per confirmed connectivity generation without duplicating the
  // initial request or reacting to unrelated Library URL changes.
  useBoundedQueryRecovery(query);
  return query;
}


export function setUserSettingsQueryData(user, payload) {
  if (!user?.id || !payload || typeof payload !== "object") {
    return payload;
  }
  queryClient.setQueryData(
    buildUserSettingsQueryKey({ userId: user.id, role: user.role }),
    payload,
  );
  return payload;
}
