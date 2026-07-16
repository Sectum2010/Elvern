import { queryClient } from "./queryClient";


export const LIBRARY_QUERY_STALE_TIME_MS = 5 * 60 * 1000;
export const LIBRARY_QUERY_GC_TIME_MS = 4 * 60 * 60 * 1000;
export const LIBRARY_QUERY_PREFIX = Object.freeze(["library", "v1"]);


function normalizeString(value, fallback = "") {
  const normalized = String(value ?? "").trim();
  return normalized || fallback;
}


export function normalizeLibraryQueryIdentity({
  userId,
  role,
  category,
  source,
  genre,
  quality,
  sort,
  query,
} = {}) {
  return {
    userId: normalizeString(userId),
    role: normalizeString(role).toLowerCase(),
    category: normalizeString(category, "movies").toLowerCase(),
    source: normalizeString(source, "all").toLowerCase(),
    genre: normalizeString(genre),
    quality: normalizeString(quality, "all").toLowerCase(),
    sort: normalizeString(sort, "smart").toLowerCase(),
    query: normalizeString(query),
  };
}


export function buildLibraryQueryKey(identity = {}) {
  return [
    ...LIBRARY_QUERY_PREFIX,
    normalizeLibraryQueryIdentity(identity),
  ];
}


export function isLibraryQueryKey(queryKey) {
  return Array.isArray(queryKey)
    && queryKey[0] === LIBRARY_QUERY_PREFIX[0]
    && queryKey[1] === LIBRARY_QUERY_PREFIX[1];
}


export function invalidateLibraryQueries({ refetchType = "active" } = {}) {
  return queryClient.invalidateQueries({
    queryKey: LIBRARY_QUERY_PREFIX,
    refetchType,
  });
}


export function markLibraryQueriesStale({ refetchType = "none" } = {}) {
  return invalidateLibraryQueries({ refetchType });
}


function patchProgressInItems(items, itemId, progressFields) {
  if (!Array.isArray(items)) {
    return items;
  }
  let changed = false;
  const patchedItems = items.map((item) => {
    if (Number(item?.id) !== itemId) {
      return item;
    }
    changed = true;
    return {
      ...item,
      ...progressFields,
    };
  });
  return changed ? patchedItems : items;
}


function patchProgressInRails(rails, itemId, progressFields) {
  if (!Array.isArray(rails)) {
    return rails;
  }
  let changed = false;
  const patchedRails = rails.map((rail) => {
    const patchedItems = patchProgressInItems(rail?.items, itemId, progressFields);
    if (patchedItems === rail?.items) {
      return rail;
    }
    changed = true;
    return {
      ...rail,
      items: patchedItems,
    };
  });
  return changed ? patchedRails : rails;
}


function patchLibraryPayloadProgress(payload, itemId, progressFields) {
  if (!payload || typeof payload !== "object") {
    return payload;
  }
  const patched = {
    items: patchProgressInItems(payload.items, itemId, progressFields),
    series_rails: patchProgressInRails(payload.series_rails, itemId, progressFields),
    cloud_series_rails: patchProgressInRails(payload.cloud_series_rails, itemId, progressFields),
    continue_watching: patchProgressInItems(payload.continue_watching, itemId, progressFields),
    recently_added: patchProgressInItems(payload.recently_added, itemId, progressFields),
  };
  const changed = Object.entries(patched).some(([fieldName, value]) => value !== payload[fieldName]);
  return changed ? { ...payload, ...patched } : payload;
}


export async function patchLibraryProgressCaches(
  progressPayload,
  { refetchActiveOnCompletion = false } = {},
) {
  const itemId = Number(progressPayload?.media_item_id ?? progressPayload?.item_id);
  if (!Number.isFinite(itemId) || itemId <= 0) {
    return { patchedQueryCount: 0, markedStale: false, activeRefetched: false };
  }
  const progressFields = {
    progress_seconds: Number(progressPayload?.position_seconds ?? progressPayload?.progress_seconds ?? 0) || 0,
    progress_duration_seconds: progressPayload?.duration_seconds
      ?? progressPayload?.progress_duration_seconds
      ?? null,
    completed: Boolean(progressPayload?.completed),
  };
  let patchedQueryCount = 0;
  queryClient.getQueryCache().findAll().forEach((query) => {
    if (!isLibraryQueryKey(query.queryKey) || query.state.data === undefined) {
      return;
    }
    const nextPayload = patchLibraryPayloadProgress(query.state.data, itemId, progressFields);
    if (nextPayload !== query.state.data) {
      patchedQueryCount += 1;
      queryClient.setQueryData(query.queryKey, nextPayload);
    }
  });
  await markLibraryQueriesStale({ refetchType: "none" });
  const activeRefetched = Boolean(progressFields.completed && refetchActiveOnCompletion);
  if (activeRefetched) {
    await queryClient.refetchQueries({
      queryKey: LIBRARY_QUERY_PREFIX,
      type: "active",
    });
  }
  return { patchedQueryCount, markedStale: true, activeRefetched };
}
