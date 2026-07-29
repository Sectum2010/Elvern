import { canonicalizeSpaPathname } from "./canonicalSpaPath.js";

const LIBRARY_RETURN_STORAGE_KEY = "elvern:library-return-target";

function normalizePositiveNumber(value, fallback = null) {
  const parsedValue = Number(value);
  return Number.isFinite(parsedValue) && parsedValue > 0 ? parsedValue : fallback;
}

function normalizeNonNegativeNumber(value, fallback = 0) {
  if (value === undefined || value === null || value === "") {
    return fallback;
  }
  const parsedValue = Number(value);
  return Number.isFinite(parsedValue) && parsedValue >= 0 ? parsedValue : fallback;
}

function normalizeFiniteNumber(value, fallback = null) {
  if (value === undefined || value === null || value === "") {
    return fallback;
  }
  const parsedValue = Number(value);
  return Number.isFinite(parsedValue) ? parsedValue : fallback;
}

function normalizeString(value) {
  if (value === undefined || value === null) {
    return null;
  }
  const normalizedValue = String(value).trim();
  return normalizedValue ? normalizedValue : null;
}

export function normalizeLibraryListPath(pathname = "") {
  const rawPath = String(pathname || "");
  const hashIndex = rawPath.indexOf("#");
  const hash = hashIndex >= 0 ? rawPath.slice(hashIndex) : "";
  const normalizedPath = hashIndex >= 0 ? rawPath.slice(0, hashIndex) : rawPath;
  const queryIndex = normalizedPath.indexOf("?");
  const pathOnly = canonicalizeSpaPathname(
    queryIndex >= 0 ? normalizedPath.slice(0, queryIndex) : normalizedPath,
  );
  const search = queryIndex >= 0 ? normalizedPath.slice(queryIndex) : "";
  if (pathOnly === "/library/local" || pathOnly === "/library/cloud") {
    const params = new URLSearchParams(search);
    params.set("source", pathOnly.endsWith("/cloud") ? "cloud" : "local");
    return `/library?${params.toString()}${hash}`;
  }
  if (pathOnly === "/library") {
    return `${search ? `/library${search}` : "/library"}${hash}`;
  }
  return "/library";
}

export function normalizeLibraryReturnTarget(payload = {}) {
  if (!payload || typeof payload !== "object") {
    return null;
  }
  const target = {
    listPath: normalizeLibraryListPath(payload.listPath),
    anchorItemId: normalizePositiveNumber(payload.anchorItemId),
    anchorInstanceKey: normalizeString(payload.anchorInstanceKey),
    scrollY: normalizeNonNegativeNumber(payload.scrollY, 0),
    pendingRestore: Boolean(payload.pendingRestore),
    anchorViewportRatioY: normalizeFiniteNumber(payload.anchorViewportRatioY),
    anchorViewportRatioX: normalizeFiniteNumber(payload.anchorViewportRatioX),
    viewportWidth: normalizePositiveNumber(payload.viewportWidth),
    viewportHeight: normalizePositiveNumber(payload.viewportHeight),
    railKey: normalizeString(payload.railKey),
    railScrollLeft: normalizeNonNegativeNumber(payload.railScrollLeft, null),
  };
  const userId = normalizeString(payload.userId);
  const role = normalizeString(payload.role)?.toLowerCase() || null;
  if (userId !== null || role !== null) {
    target.userId = userId;
    target.role = role;
  }
  return target;
}

function mergeStoredReturnTarget(locationTarget, storedTarget) {
  if (!locationTarget || !storedTarget || locationTarget.listPath !== storedTarget.listPath) {
    return locationTarget;
  }
  const sameInstance = Boolean(
    locationTarget.anchorInstanceKey
      && storedTarget.anchorInstanceKey
      && locationTarget.anchorInstanceKey === storedTarget.anchorInstanceKey,
  );
  const sameItem = Boolean(
    locationTarget.anchorItemId
      && storedTarget.anchorItemId
      && locationTarget.anchorItemId === storedTarget.anchorItemId,
  );
  if (!sameInstance && !sameItem) {
    return locationTarget;
  }
  return {
    ...locationTarget,
    anchorInstanceKey: locationTarget.anchorInstanceKey || storedTarget.anchorInstanceKey,
    anchorViewportRatioY: Number.isFinite(locationTarget.anchorViewportRatioY)
      ? locationTarget.anchorViewportRatioY
      : storedTarget.anchorViewportRatioY,
    anchorViewportRatioX: Number.isFinite(locationTarget.anchorViewportRatioX)
      ? locationTarget.anchorViewportRatioX
      : storedTarget.anchorViewportRatioX,
    viewportWidth: locationTarget.viewportWidth || storedTarget.viewportWidth,
    viewportHeight: locationTarget.viewportHeight || storedTarget.viewportHeight,
    railKey: locationTarget.railKey || storedTarget.railKey,
    railScrollLeft: Number.isFinite(locationTarget.railScrollLeft)
      ? locationTarget.railScrollLeft
      : storedTarget.railScrollLeft,
    scrollY: locationTarget.scrollY || storedTarget.scrollY,
    pendingRestore: locationTarget.pendingRestore || storedTarget.pendingRestore,
  };
}

export function buildLibraryReturnState(payload = {}) {
  const normalizedTarget = normalizeLibraryReturnTarget(payload);
  return {
    libraryReturn: normalizedTarget,
  };
}

export function extractLibraryReturnState(locationState, identity = {}) {
  const payload = locationState?.libraryReturn;
  if (!payload) {
    return null;
  }
  const normalizedTarget = normalizeLibraryReturnTarget(payload);
  if (!returnTargetMatchesIdentity(normalizedTarget, identity)) {
    return null;
  }
  return mergeStoredReturnTarget(
    normalizedTarget,
    readLibraryReturnTarget(identity),
  );
}

function returnTargetMatchesIdentity(target, { userId, role } = {}) {
  if (userId === undefined && role === undefined) {
    return true;
  }
  const normalizedUserId = normalizeString(userId);
  const normalizedRole = normalizeString(role)?.toLowerCase() || null;
  return Boolean(
    target?.userId
    && target?.role
    && target.userId === normalizedUserId
    && target.role === normalizedRole
  );
}

export function readLibraryReturnTarget(identity = {}) {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const raw = window.sessionStorage.getItem(LIBRARY_RETURN_STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const payload = JSON.parse(raw);
    if (!payload) {
      return null;
    }
    const normalizedTarget = normalizeLibraryReturnTarget(payload);
    return returnTargetMatchesIdentity(normalizedTarget, identity)
      ? normalizedTarget
      : null;
  } catch {
    return null;
  }
}

export function rememberLibraryReturnTarget({
  listPath,
  anchorItemId = null,
  anchorInstanceKey = null,
  scrollY = 0,
  pendingRestore = false,
  anchorViewportRatioY = null,
  anchorViewportRatioX = null,
  viewportWidth = null,
  viewportHeight = null,
  railKey = null,
  railScrollLeft = null,
  userId = null,
  role = null,
} = {}) {
  if (typeof window === "undefined") {
    return null;
  }
  const payload = normalizeLibraryReturnTarget({
    listPath,
    anchorItemId,
    anchorInstanceKey,
    scrollY,
    pendingRestore,
    anchorViewportRatioY,
    anchorViewportRatioX,
    viewportWidth,
    viewportHeight,
    railKey,
    railScrollLeft,
    userId,
    role,
  });
  try {
    window.sessionStorage.setItem(LIBRARY_RETURN_STORAGE_KEY, JSON.stringify(payload));
  } catch {
    // Ignore sessionStorage failures and fall back to plain navigation.
  }
  return payload;
}

export function markLibraryReturnPending(identity = {}) {
  const current = readLibraryReturnTarget(identity);
  if (!current) {
    return null;
  }
  return rememberLibraryReturnTarget({ ...current, pendingRestore: true });
}

export function clearLibraryReturnPending(identity = {}) {
  const current = readLibraryReturnTarget(identity);
  if (!current) {
    return null;
  }
  return rememberLibraryReturnTarget({ ...current, pendingRestore: false });
}
