const LIBRARY_RETURN_STORAGE_KEY = "elvern:library-return-target";

function normalizePositiveNumber(value, fallback = null) {
  const parsedValue = Number(value);
  return Number.isFinite(parsedValue) && parsedValue > 0 ? parsedValue : fallback;
}

function normalizeNonNegativeNumber(value, fallback = 0) {
  const parsedValue = Number(value);
  return Number.isFinite(parsedValue) && parsedValue >= 0 ? parsedValue : fallback;
}

function normalizeFiniteNumber(value, fallback = null) {
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
  if (pathname === "/library/local" || pathname === "/library/cloud") {
    return pathname;
  }
  return "/library";
}

export function normalizeLibraryReturnTarget(payload = {}) {
  if (!payload || typeof payload !== "object") {
    return null;
  }
  return {
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

export function extractLibraryReturnState(locationState) {
  const payload = locationState?.libraryReturn;
  if (!payload) {
    return null;
  }
  const normalizedTarget = normalizeLibraryReturnTarget(payload);
  return mergeStoredReturnTarget(normalizedTarget, readLibraryReturnTarget());
}

export function readLibraryReturnTarget() {
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
    return normalizeLibraryReturnTarget(payload);
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
  });
  try {
    window.sessionStorage.setItem(LIBRARY_RETURN_STORAGE_KEY, JSON.stringify(payload));
  } catch {
    // Ignore sessionStorage failures and fall back to plain navigation.
  }
  return payload;
}

export function markLibraryReturnPending() {
  const current = readLibraryReturnTarget();
  if (!current) {
    return null;
  }
  return rememberLibraryReturnTarget({ ...current, pendingRestore: true });
}

export function clearLibraryReturnPending() {
  const current = readLibraryReturnTarget();
  if (!current) {
    return null;
  }
  return rememberLibraryReturnTarget({ ...current, pendingRestore: false });
}
