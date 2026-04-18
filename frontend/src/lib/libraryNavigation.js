const LIBRARY_RETURN_STORAGE_KEY = "elvern:library-return-target";


export function normalizeLibraryListPath(pathname = "") {
  if (pathname === "/library/local" || pathname === "/library/cloud") {
    return pathname;
  }
  return "/library";
}

export function buildLibraryReturnState({ listPath, anchorItemId, scrollY } = {}) {
  const normalizedPath = normalizeLibraryListPath(listPath);
  const parsedAnchorItemId = Number(anchorItemId);
  const parsedScrollY = Number(scrollY);
  return {
    libraryReturn: {
      listPath: normalizedPath,
      anchorItemId: Number.isFinite(parsedAnchorItemId) && parsedAnchorItemId > 0 ? parsedAnchorItemId : null,
      scrollY: Number.isFinite(parsedScrollY) && parsedScrollY >= 0 ? parsedScrollY : 0,
    },
  };
}

export function extractLibraryReturnState(locationState) {
  const payload = locationState?.libraryReturn;
  if (!payload) {
    return null;
  }
  return {
    listPath: normalizeLibraryListPath(payload.listPath),
    anchorItemId:
      Number.isFinite(Number(payload.anchorItemId)) && Number(payload.anchorItemId) > 0
        ? Number(payload.anchorItemId)
        : null,
    scrollY:
      Number.isFinite(Number(payload.scrollY)) && Number(payload.scrollY) >= 0
        ? Number(payload.scrollY)
        : 0,
  };
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
    return {
      listPath: normalizeLibraryListPath(payload.listPath),
      anchorItemId:
        Number.isFinite(Number(payload.anchorItemId)) && Number(payload.anchorItemId) > 0
          ? Number(payload.anchorItemId)
          : null,
      scrollY:
        Number.isFinite(Number(payload.scrollY)) && Number(payload.scrollY) >= 0
          ? Number(payload.scrollY)
          : 0,
      pendingRestore: Boolean(payload.pendingRestore),
    };
  } catch {
    return null;
  }
}

export function rememberLibraryReturnTarget({
  listPath,
  anchorItemId = null,
  scrollY = 0,
  pendingRestore = false,
} = {}) {
  if (typeof window === "undefined") {
    return null;
  }
  const payload = {
    listPath: normalizeLibraryListPath(listPath),
    anchorItemId:
      Number.isFinite(Number(anchorItemId)) && Number(anchorItemId) > 0
        ? Number(anchorItemId)
        : null,
    scrollY:
      Number.isFinite(Number(scrollY)) && Number(scrollY) >= 0
        ? Number(scrollY)
        : 0,
    pendingRestore: Boolean(pendingRestore),
  };
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
