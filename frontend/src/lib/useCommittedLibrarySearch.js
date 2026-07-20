import { useCallback, useEffect, useMemo, useReducer, useState } from "react";


const FLOATING_SEARCH_EXPANDED_STORAGE_PREFIX = "elvern:floating-library-search-expanded:v1:";
const SEARCH_DRAFT_SOURCES = new Set(["static", "floating"]);


function createSearchDraftState(committedQuery) {
  return {
    staticDraft: committedQuery,
    floatingDraft: committedQuery,
    activeDraftSource: null,
  };
}


function sourceOwnsDraft(state, source) {
  return SEARCH_DRAFT_SOURCES.has(source)
    && (!state.activeDraftSource || state.activeDraftSource === source);
}


export function committedLibrarySearchReducer(state, action) {
  switch (action.type) {
    case "URL_SYNC":
    case "COMMIT":
    case "CLEAR":
      return createSearchDraftState(String(action.query || ""));
    case "UPDATE_DRAFT":
      if (!sourceOwnsDraft(state, action.source)) return state;
      return {
        ...state,
        [`${action.source}Draft`]: String(action.value ?? ""),
        activeDraftSource: action.source,
      };
    case "REVERT":
      if (!sourceOwnsDraft(state, action.source)) return state;
      return createSearchDraftState(String(action.query || ""));
    case "RELEASE_LOCK":
      if (!sourceOwnsDraft(state, action.source)) return state;
      return { ...state, activeDraftSource: null };
    default:
      return state;
  }
}


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
  const [draftState, dispatchDraft] = useReducer(
    committedLibrarySearchReducer,
    normalizedCommittedQuery,
    createSearchDraftState,
  );
  const [floatingExpanded, setFloatingExpanded] = useState(() => readExpanded(storage, pathname));

  useEffect(() => {
    dispatchDraft({ type: "URL_SYNC", query: normalizedCommittedQuery });
  }, [location?.search, normalizedCommittedQuery]);

  useEffect(() => {
    setFloatingExpanded(readExpanded(storage, pathname));
  }, [pathname, storage]);

  const replaceCommittedQuery = useCallback((source, nextQuery, actionType = "COMMIT") => {
    if (!sourceOwnsDraft(draftState, source)) {
      return;
    }
    const normalized = normalizeCommittedQuery(nextQuery);
    const nextSearch = buildCommittedSearch(location?.search, normalized);
    dispatchDraft({ type: actionType, query: normalized });
    if (nextSearch === String(location?.search || "")) {
      return;
    }
    navigate({
      pathname,
      search: nextSearch,
      hash: location?.hash || "",
    }, { replace: true });
  }, [draftState, location?.hash, location?.search, navigate, pathname]);

  const updateDraft = useCallback((source, value) => {
    dispatchDraft({ type: "UPDATE_DRAFT", source, value });
  }, []);

  const commit = useCallback((source) => {
    replaceCommittedQuery(source, draftState[`${source}Draft`]);
  }, [draftState, replaceCommittedQuery]);

  const revert = useCallback((source) => {
    dispatchDraft({ type: "REVERT", source, query: normalizedCommittedQuery });
  }, [normalizedCommittedQuery]);

  const clear = useCallback((source) => {
    replaceCommittedQuery(source, "", "CLEAR");
  }, [replaceCommittedQuery]);

  const toggleFloatingExpanded = useCallback(() => {
    setFloatingExpanded((current) => {
      const next = !current;
      writeExpanded(storage, pathname, next);
      return next;
    });
  }, [pathname, storage]);

  return useMemo(() => ({
    activeDraftSource: draftState.activeDraftSource,
    clear,
    commit,
    committedQuery: normalizedCommittedQuery,
    floatingDraft: draftState.floatingDraft,
    floatingExpanded,
    isSourceLocked: (source) => Boolean(
      draftState.activeDraftSource && draftState.activeDraftSource !== source
    ),
    revert,
    staticDraft: draftState.staticDraft,
    toggleFloatingExpanded,
    updateDraft,
  }), [
    clear,
    commit,
    draftState,
    floatingExpanded,
    normalizedCommittedQuery,
    revert,
    toggleFloatingExpanded,
    updateDraft,
  ]);
}
