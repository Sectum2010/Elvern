import { queryClient } from "./queryClient";


export const LIBRARY_QUERY_STALE_TIME_MS = 5 * 60 * 1000;
export const LIBRARY_QUERY_GC_TIME_MS = 4 * 60 * 60 * 1000;
export const LIBRARY_QUERY_PREFIX = Object.freeze(["library", "v1"]);
export const LIBRARY_V2_QUERY_PREFIX = Object.freeze(["library", "v2"]);
export const LIBRARY_SHADOW_V2_QUERY_PREFIX = Object.freeze(["library", "shadow-v2"]);
export const LIBRARY_ALL_QUERY_PREFIX = Object.freeze(["library"]);


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


export function buildLibraryV2QueryKey(identity = {}) {
  return [
    ...LIBRARY_V2_QUERY_PREFIX,
    normalizeLibraryQueryIdentity({ ...identity, query: "" }),
  ];
}


export function buildLibraryShadowV2QueryKey(identity = {}) {
  return [
    ...LIBRARY_SHADOW_V2_QUERY_PREFIX,
    normalizeLibraryQueryIdentity({ ...identity, query: "" }),
  ];
}


export function isLibraryQueryKey(queryKey) {
  return Array.isArray(queryKey)
    && queryKey[0] === LIBRARY_ALL_QUERY_PREFIX[0]
    && ["v1", "v2", "shadow-v2"].includes(queryKey[1]);
}


export function isLibraryV2QueryKey(queryKey) {
  return Array.isArray(queryKey)
    && queryKey[0] === LIBRARY_V2_QUERY_PREFIX[0]
    && [LIBRARY_V2_QUERY_PREFIX[1], LIBRARY_SHADOW_V2_QUERY_PREFIX[1]].includes(queryKey[1]);
}


export function isLibraryRenderQueryKey(queryKey) {
  return isLibraryQueryKey(queryKey) && queryKey[1] !== LIBRARY_SHADOW_V2_QUERY_PREFIX[1];
}


export function invalidateLibraryQueries({ refetchType = "active" } = {}) {
  return queryClient.invalidateQueries({
    queryKey: LIBRARY_ALL_QUERY_PREFIX,
    refetchType,
  });
}


export function markLibraryQueriesStale({ refetchType = "none" } = {}) {
  return invalidateLibraryQueries({ refetchType });
}


function toDetailPreview(item) {
  if (!item || typeof item !== "object") {
    return null;
  }
  const id = Number(item.id);
  if (!Number.isFinite(id) || id <= 0 || !String(item.title || "").trim()) {
    return null;
  }
  return {
    id,
    title: String(item.title).trim(),
    year: item.year ?? null,
    source_kind: String(item.source_kind || "").trim() || null,
  };
}


function findItemInV1Payload(payload, itemId) {
  const collections = [
    payload?.items,
    payload?.continue_watching,
    payload?.recently_added,
    ...(Array.isArray(payload?.series_rails) ? payload.series_rails.map((rail) => rail?.items) : []),
    ...(Array.isArray(payload?.cloud_series_rails) ? payload.cloud_series_rails.map((rail) => rail?.items) : []),
  ];
  for (const items of collections) {
    const match = Array.isArray(items)
      ? items.find((item) => Number(item?.id) === itemId)
      : null;
    if (match) {
      return match;
    }
  }
  return null;
}


export function findLibraryItemDetailPreview({ itemId, userId, role } = {}) {
  const normalizedItemId = Number(itemId);
  const normalizedUserId = normalizeString(userId);
  const normalizedRole = normalizeString(role).toLowerCase();
  if (!Number.isFinite(normalizedItemId) || normalizedItemId <= 0 || !normalizedUserId || !normalizedRole) {
    return null;
  }
  const queries = queryClient.getQueryCache().findAll();
  for (const query of queries) {
    if (!isLibraryRenderQueryKey(query.queryKey)) {
      continue;
    }
    const identity = query.queryKey[2] || {};
    if (
      normalizeString(identity.userId) !== normalizedUserId
      || normalizeString(identity.role).toLowerCase() !== normalizedRole
    ) {
      continue;
    }
    const payload = query.state.data;
    const item = query.queryKey[1] === "v2"
      ? payload?.items_by_id?.[String(normalizedItemId)]
      : findItemInV1Payload(payload, normalizedItemId);
    const preview = toDetailPreview(item);
    if (preview) {
      return preview;
    }
  }
  return null;
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


function patchLibrarySummaryV2Progress(payload, itemId, progressFields) {
  if (payload?.schema_version !== "library-summary-v2" || !payload.items_by_id) {
    return payload;
  }
  const itemKey = String(itemId);
  const currentItem = payload.items_by_id[itemKey];
  if (!currentItem) {
    return payload;
  }
  return {
    ...payload,
    items_by_id: {
      ...payload.items_by_id,
      [itemKey]: {
        ...currentItem,
        ...progressFields,
      },
    },
  };
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
    const nextPayload = isLibraryV2QueryKey(query.queryKey)
      ? patchLibrarySummaryV2Progress(query.state.data, itemId, progressFields)
      : patchLibraryPayloadProgress(query.state.data, itemId, progressFields);
    if (nextPayload !== query.state.data) {
      patchedQueryCount += 1;
      queryClient.setQueryData(query.queryKey, nextPayload);
    }
  });
  await markLibraryQueriesStale({ refetchType: "none" });
  const activeRefetched = Boolean(progressFields.completed && refetchActiveOnCompletion);
  if (activeRefetched) {
    await queryClient.refetchQueries({
      predicate: (query) => isLibraryRenderQueryKey(query.queryKey),
      type: "active",
    });
  }
  return { patchedQueryCount, markedStale: true, activeRefetched };
}
