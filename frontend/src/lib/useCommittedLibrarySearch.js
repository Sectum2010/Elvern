import { useCallback, useEffect, useMemo, useState } from "react";


const FLOATING_SEARCH_EXPANDED_STORAGE_PREFIX = "elvern:floating-library-search-expanded:v1:";
const SEARCH_DRAFT_SOURCES = new Set(["static", "floating"]);


function normalizeCommittedQuery(value) {
  return String(value || "").trim();
}


function buildCommittedSearch(currentSearch, query) {
  const params = new URLSearchParams(currentSearch || "");
  const normalized = normalizeCommittedQuery(query);
  if (normalized) {
    params.set("q", normalized);
  } else {
    params.delete("q");
  }
  const serialized = params.toString();
  return serialized ? `?${serialized}` : "";
}


function storageKeyForPath(pathname) {
  return `${FLOATING_SEARCH_EXPANDED_STORAGE_PREFIX}${encodeURIComponent(String(pathname || "/library"))}`;
}


function readExpanded(storage, pathname) {
  try {
    return storage?.getItem(storageKeyForPath(pathname)) === "1";
  } catch {
    return false;
  }
}


function writeExpanded(storage, pathname, expanded) {
  try {
    storage?.setItem(storageKeyForPath(pathname), expanded ? "1" : "0");
  } catch {
    // Session-only UI state is optional when storage is unavailable.
  }
}


export function shouldCommitLibrarySearchKey(event) {
  return event?.key === "Enter"
    && !event?.isComposing
    && !event?.nativeEvent?.isComposing
    && event?.keyCode !== 229;
}


export function useCommittedLibrarySearch({
  committedQuery,
  location,
  navigate,
  storage = globalThis?.sessionStorage,
} = {}) {
  const normalizedCommittedQuery = normalizeCommittedQuery(committedQuery);
  const pathname = String(location?.pathname || "/library");
  const [staticDraft, setStaticDraft] = useState(normalizedCommittedQuery);
  const [floatingDraft, setFloatingDraft] = useState(normalizedCommittedQuery);
  const [activeDraftSource, setActiveDraftSource] = useState(null);
  const [floatingExpanded, setFloatingExpanded] = useState(() => readExpanded(storage, pathname));

  useEffect(() => {
    setStaticDraft(normalizedCommittedQuery);
    setFloatingDraft(normalizedCommittedQuery);
    setActiveDraftSource(null);
  }, [location?.search, normalizedCommittedQuery]);

  useEffect(() => {
    setFloatingExpanded(readExpanded(storage, pathname));
  }, [pathname, storage]);

  const replaceCommittedQuery = useCallback((nextQuery) => {
    const normalized = normalizeCommittedQuery(nextQuery);
    const nextSearch = buildCommittedSearch(location?.search, normalized);
    setStaticDraft(normalized);
    setFloatingDraft(normalized);
    setActiveDraftSource(null);
    if (nextSearch === String(location?.search || "")) {
      return;
    }
    navigate({
      pathname,
      search: nextSearch,
      hash: location?.hash || "",
    }, { replace: true });
  }, [location?.hash, location?.search, navigate, pathname]);

  const updateDraft = useCallback((source, value) => {
    if (!SEARCH_DRAFT_SOURCES.has(source)) {
      return;
    }
    setActiveDraftSource((current) => {
      if (current && current !== source) {
        return current;
      }
      if (source === "static") {
        setStaticDraft(String(value ?? ""));
      } else {
        setFloatingDraft(String(value ?? ""));
      }
      return source;
    });
  }, []);

  const commit = useCallback((source) => {
    replaceCommittedQuery(source === "floating" ? floatingDraft : staticDraft);
  }, [floatingDraft, replaceCommittedQuery, staticDraft]);

  const revert = useCallback(() => {
    setStaticDraft(normalizedCommittedQuery);
    setFloatingDraft(normalizedCommittedQuery);
    setActiveDraftSource(null);
  }, [normalizedCommittedQuery]);

  const clear = useCallback(() => {
    replaceCommittedQuery("");
  }, [replaceCommittedQuery]);

  const toggleFloatingExpanded = useCallback(() => {
    setFloatingExpanded((current) => {
      const next = !current;
      writeExpanded(storage, pathname, next);
      return next;
    });
  }, [pathname, storage]);

  return useMemo(() => ({
    activeDraftSource,
    clear,
    commit,
    committedQuery: normalizedCommittedQuery,
    floatingDraft,
    floatingExpanded,
    isSourceLocked: (source) => Boolean(activeDraftSource && activeDraftSource !== source),
    revert,
    staticDraft,
    toggleFloatingExpanded,
    updateDraft,
  }), [
    activeDraftSource,
    clear,
    commit,
    floatingDraft,
    floatingExpanded,
    normalizedCommittedQuery,
    revert,
    staticDraft,
    toggleFloatingExpanded,
    updateDraft,
  ]);
}
