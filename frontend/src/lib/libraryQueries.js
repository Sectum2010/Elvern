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
