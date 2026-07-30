import {
  Crown,
  LogOut,
  MessageSquare,
  Search,
  Settings,
  Shield,
  SlidersHorizontal,
} from "lucide-react";
import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { canAccessAssistant, resolveAssistantNavigationTarget } from "../lib/assistantAccess.js";
import {
  DEFAULT_LIBRARY_ARRANGE,
  LIBRARY_CATEGORY_OPTIONS,
  LIBRARY_QUALITY_OPTIONS,
  LIBRARY_SORT_OPTIONS,
  LIBRARY_SOURCE_OPTIONS,
  buildLibraryViewSearch,
  countLibraryArrangeFilters,
  libraryArrangeEquals,
  librarySortDirectionLabel,
  normalizeLibraryArrange,
  normalizeLibraryGenres,
  resolveLibraryArrangeFromSearch,
  resolveLibraryCategoryFromSearch,
  resolveLibraryQueryFromSearch,
  toggleLibrarySort,
} from "../lib/desktopLibraryViewState.js";
import {
  markLibraryReturnPending,
  readLibraryReturnTarget,
} from "../lib/libraryNavigation.js";


function joinPathAndSearch(pathname, search, hash = "") {
  return `${pathname}${search || ""}${hash || ""}`;
}


function locationFromListPath(listPath) {
  try {
    const parsed = new URL(String(listPath || "/library"), "https://elvern.invalid");
    return {
      pathname: parsed.pathname,
      search: parsed.search,
      hash: parsed.hash,
    };
  } catch {
    return { pathname: "/library", search: "?category=movies", hash: "" };
  }
}


export function resolveDesktopLibraryIslandView({
  location,
  libraryState,
  user,
} = {}) {
  if (location?.pathname === "/library") {
    return {
      pathname: "/library",
      search: location.search || "",
      hash: location.hash || "",
      fromDetail: false,
    };
  }
  const remembered = readLibraryReturnTarget({
    userId: user?.id,
    role: user?.role,
  });
  if (remembered?.listPath) {
    return {
      ...locationFromListPath(remembered.listPath),
      fromDetail: true,
    };
  }
  if (libraryState?.listPath) {
    return {
      ...locationFromListPath(libraryState.listPath),
      fromDetail: true,
    };
  }
  return {
    pathname: "/library",
    search: "?category=movies",
    hash: "",
    fromDetail: true,
  };
}


function DesktopLibrarySourceControl({ onChange, value }) {
  const controlRef = useRef(null);
  const draggingRef = useRef(false);
  const ignoreNextClickRef = useRef(false);
  const ignoreClickTimerRef = useRef(0);
  const dragBoundsRef = useRef({ clientX: 0, min: 0, max: 0 });
  const [dragging, setDragging] = useState(false);
  const [dragOffset, setDragOffset] = useState(0);
  const [dragPreviewValue, setDragPreviewValue] = useState(null);

  useEffect(() => () => {
    window.clearTimeout(ignoreClickTimerRef.current);
  }, []);

  function getValueFromPoint(clientX, clientY, { allowOutside = false } = {}) {
    const rect = controlRef.current?.getBoundingClientRect();
    if (!rect || !LIBRARY_SOURCE_OPTIONS.length) {
      return allowOutside ? value : null;
    }
    const outside = clientX < rect.left
      || clientX > rect.right
      || clientY < rect.top
      || clientY > rect.bottom;
    if (outside && !allowOutside) {
      return null;
    }
    const ratio = Math.max(0, Math.min(0.999, (clientX - rect.left) / (rect.width || 1)));
    const index = Math.max(
      0,
      Math.min(LIBRARY_SOURCE_OPTIONS.length - 1, Math.floor(ratio * LIBRARY_SOURCE_OPTIONS.length)),
    );
    return LIBRARY_SOURCE_OPTIONS[index]?.key || value;
  }

  function resetDrag() {
    draggingRef.current = false;
    setDragging(false);
    setDragOffset(0);
    setDragPreviewValue(null);
  }

  function handleActivePointerDown(event) {
    event.preventDefault();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    const sourceButtons = controlRef.current?.querySelectorAll("button");
    const firstButtonRect = sourceButtons?.[0]?.getBoundingClientRect();
    const lastButtonRect = sourceButtons?.[sourceButtons.length - 1]?.getBoundingClientRect();
    const buttonRect = event.currentTarget.getBoundingClientRect();
    dragBoundsRef.current = {
      clientX: event.clientX,
      min: firstButtonRect ? firstButtonRect.left - buttonRect.left : 0,
      max: lastButtonRect ? lastButtonRect.right - buttonRect.right : 0,
    };
    draggingRef.current = true;
    ignoreNextClickRef.current = true;
    setDragOffset(0);
    setDragPreviewValue(value);
    setDragging(true);
  }

  function handleActivePointerMove(event) {
    if (!draggingRef.current) {
      return;
    }
    const bounds = dragBoundsRef.current;
    const nextOffset = Math.max(bounds.min, Math.min(bounds.max, event.clientX - bounds.clientX));
    setDragOffset(nextOffset);
    setDragPreviewValue(getValueFromPoint(event.clientX, event.clientY, { allowOutside: true }));
  }

  function handleActivePointerUp(event) {
    if (!draggingRef.current) {
      return;
    }
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    const nextValue = getValueFromPoint(event.clientX, event.clientY);
    resetDrag();
    if (nextValue) {
      onChange(nextValue);
    }
    window.clearTimeout(ignoreClickTimerRef.current);
    ignoreClickTimerRef.current = window.setTimeout(() => {
      ignoreNextClickRef.current = false;
    }, 120);
  }

  function handleActivePointerCancel(event) {
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    ignoreNextClickRef.current = false;
    resetDrag();
  }

  const selectedIndex = Math.max(
    0,
    LIBRARY_SOURCE_OPTIONS.findIndex((option) => option.key === value),
  );
  const controlStyle = {
    "--desktop-source-count": LIBRARY_SOURCE_OPTIONS.length,
    "--desktop-source-index": selectedIndex,
    "--desktop-source-drag-x": dragging ? `${dragOffset}px` : "0px",
  };

  return (
    <div
      aria-label="Library source"
      className={[
        "desktop-library-island__segments",
        dragging ? "desktop-library-island__segments--dragging" : "",
      ].filter(Boolean).join(" ")}
      ref={controlRef}
      role="radiogroup"
      style={controlStyle}
    >
      <span aria-hidden="true" className="desktop-library-island__segments-indicator" />
      {LIBRARY_SOURCE_OPTIONS.map((option) => {
        const selected = value === option.key;
        const visuallySelected = dragging
          ? dragPreviewValue === option.key
          : selected;
        return (
          <button
            aria-checked={selected}
            className={[
              visuallySelected ? "is-active" : "",
              selected && dragging ? "is-dragging" : "",
            ].filter(Boolean).join(" ")}
            key={option.key}
            onClick={(event) => {
              if (ignoreNextClickRef.current) {
                event.preventDefault();
                ignoreNextClickRef.current = false;
                return;
              }
              onChange(option.key);
            }}
            onPointerCancel={selected ? handleActivePointerCancel : undefined}
            onPointerDown={selected ? handleActivePointerDown : undefined}
            onPointerMove={selected ? handleActivePointerMove : undefined}
            onPointerUp={selected ? handleActivePointerUp : undefined}
            role="radio"
            type="button"
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}


function DesktopLibraryArrangePanel({
  availableGenres,
  direction,
  draft,
  onDone,
  onReset,
  onUpdate,
  panelRef,
}) {
  const [showAllGenres, setShowAllGenres] = useState(false);
  const selectedGenreKeys = new Set(draft.genres.map((genre) => genre.toLocaleLowerCase()));
  const visibleGenres = normalizeLibraryGenres([
    ...draft.genres,
    ...availableGenres,
  ]);
  const collapsedGenres = visibleGenres.slice(0, 8);
  const displayedGenres = showAllGenres ? visibleGenres : collapsedGenres;
  const hiddenGenreCount = Math.max(0, visibleGenres.length - collapsedGenres.length);

  function toggleGenre(genre) {
    const key = genre.toLocaleLowerCase();
    const next = selectedGenreKeys.has(key)
      ? draft.genres.filter((value) => value.toLocaleLowerCase() !== key)
      : [...draft.genres, genre];
    onUpdate({ genres: normalizeLibraryGenres(next, visibleGenres) });
  }

  function toggleQuality(quality) {
    const next = draft.qualities.includes(quality)
      ? draft.qualities.filter((value) => value !== quality)
      : [...draft.qualities, quality];
    onUpdate({ qualities: next });
  }

  return (
    <div
      aria-label="Arrange library"
      className={[
        "desktop-library-island__popover",
        "desktop-library-island__arrange",
        `desktop-library-island__popover--${direction}`,
      ].join(" ")}
      id="desktop-library-arrange-panel"
      ref={panelRef}
      role="dialog"
    >
      <div className="desktop-library-island__arrange-body">
        <section className="desktop-library-island__group">
          <h3>Source</h3>
          <DesktopLibrarySourceControl
            onChange={(source) => onUpdate({ source })}
            value={draft.source}
          />
        </section>

        <section className="desktop-library-island__group">
          <h3>Genre</h3>
          <div className="desktop-library-island__chips">
            <button
              aria-pressed={!draft.genres.length}
              className={!draft.genres.length ? "is-active" : ""}
              onClick={() => onUpdate({ genres: [] })}
              type="button"
            >
              All genres
            </button>
            {displayedGenres.map((genre) => (
              <button
                aria-pressed={selectedGenreKeys.has(genre.toLocaleLowerCase())}
                className={selectedGenreKeys.has(genre.toLocaleLowerCase()) ? "is-active" : ""}
                key={genre}
                onClick={() => toggleGenre(genre)}
                type="button"
              >
                {genre}
              </button>
            ))}
            {hiddenGenreCount > 0 ? (
              <button
                className="desktop-library-island__more"
                onClick={() => setShowAllGenres((current) => !current)}
                type="button"
              >
                {showAllGenres ? "Collapse" : `+ ${hiddenGenreCount} more`}
              </button>
            ) : null}
          </div>
        </section>

        <section className="desktop-library-island__group">
          <h3>Quality</h3>
          <div className="desktop-library-island__chips">
            <button
              aria-pressed={!draft.qualities.length}
              className={!draft.qualities.length ? "is-active" : ""}
              onClick={() => onUpdate({ qualities: [] })}
              type="button"
            >
              All
            </button>
            {LIBRARY_QUALITY_OPTIONS.map((option) => (
              <button
                aria-pressed={draft.qualities.includes(option.key)}
                className={draft.qualities.includes(option.key) ? "is-active" : ""}
                key={option.key}
                onClick={() => toggleQuality(option.key)}
                type="button"
              >
                {option.label}
              </button>
            ))}
          </div>
        </section>

        <section className="desktop-library-island__group">
          <h3>Sort</h3>
          <div className="desktop-library-island__sort-list">
            {LIBRARY_SORT_OPTIONS.map((option) => {
              const active = draft.sort === option.key || draft.sort === option.alternateKey;
              return (
                <button
                  aria-pressed={active}
                  className={active ? "is-active" : ""}
                  key={option.family}
                  onClick={() => onUpdate({ sort: toggleLibrarySort(draft.sort, option) })}
                  type="button"
                >
                  <span>{option.label}</span>
                  <small>{active ? librarySortDirectionLabel(draft.sort) : ""}</small>
                </button>
              );
            })}
          </div>
          <p className="desktop-library-island__hint">
            Tap the active row again to flip direction
          </p>
        </section>
      </div>
      <div className="desktop-library-island__arrange-footer">
        <button onClick={onReset} type="button">Reset</button>
        <button className="desktop-library-island__done" onClick={onDone} type="button">
          Done
        </button>
      </div>
    </div>
  );
}


function DesktopLibraryAvatarMenu({
  direction,
  menuRef,
  onAction,
  user,
}) {
  const initial = String(user?.username || "?").trim().slice(0, 1).toUpperCase() || "?";
  const entries = [
    { key: "settings", label: "Settings", icon: Settings, to: "/settings" },
    ...(user?.role === "admin"
      ? [{ key: "admin", label: "Admin Panel", icon: Shield, to: "/admin" }]
      : []),
    ...(canAccessAssistant(user)
      ? [{
          key: "assistant",
          label: "Assistant",
          icon: MessageSquare,
          to: resolveAssistantNavigationTarget(user),
        }]
      : []),
  ];
  return (
    <div
      aria-label="Account menu"
      className={[
        "desktop-library-island__popover",
        "desktop-library-island__avatar-menu",
        `desktop-library-island__popover--${direction}`,
      ].join(" ")}
      id="desktop-library-avatar-menu"
      ref={menuRef}
      role="menu"
    >
      <div className="desktop-library-island__avatar-header">
        <span aria-hidden="true" className="desktop-library-island__avatar desktop-library-island__avatar--header">
          {initial}
        </span>
        <span className="desktop-library-island__identity">
          <strong>{user?.username}</strong>
          {user?.role === "admin" ? (
            <Crown aria-label="Administrator" className="desktop-library-island__crown" />
          ) : null}
        </span>
      </div>
      <div className="desktop-library-island__menu-separator" />
      <div className="desktop-library-island__menu-items">
        {entries.map((entry) => {
          const Icon = entry.icon;
          return (
            <button key={entry.key} onClick={() => onAction(entry)} role="menuitem" type="button">
              <Icon aria-hidden="true" />
              {entry.label}
            </button>
          );
        })}
        <button
          className="desktop-library-island__sign-out"
          onClick={() => onAction({ key: "logout" })}
          role="menuitem"
          type="button"
        >
          <LogOut aria-hidden="true" />
          Sign out
        </button>
      </div>
    </div>
  );
}


export function DesktopLibraryIsland({
  libraryState,
  onLogout,
  position = "top",
  user,
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const islandRef = useRef(null);
  const arrangeButtonRef = useRef(null);
  const avatarButtonRef = useRef(null);
  const panelRef = useRef(null);
  const menuRef = useRef(null);
  const searchInputRef = useRef(null);
  const closingRef = useRef(false);
  const [openPanel, setOpenPanel] = useState("");
  const [draftArrange, setDraftArrange] = useState(DEFAULT_LIBRARY_ARRANGE);
  const [searchDraft, setSearchDraft] = useState("");
  const view = resolveDesktopLibraryIslandView({ location, libraryState, user });
  const committedCategory = useMemo(
    () => resolveLibraryCategoryFromSearch(view.search),
    [view.search],
  );
  const committedArrange = useMemo(
    () => resolveLibraryArrangeFromSearch(view.search, libraryState?.availableGenres),
    [libraryState?.availableGenres, view.search],
  );
  const committedQuery = useMemo(
    () => resolveLibraryQueryFromSearch(view.search),
    [view.search],
  );
  const visibleArrange = openPanel === "arrange" ? draftArrange : committedArrange;
  const badgeCount = countLibraryArrangeFilters(visibleArrange);
  const initial = String(user?.username || "?").trim().slice(0, 1).toUpperCase() || "?";
  const direction = position === "bottom" ? "up" : "down";

  useEffect(() => {
    setSearchDraft(committedQuery);
  }, [committedQuery]);

  function navigateToView({
    arrange = committedArrange,
    category = committedCategory,
    query = committedQuery,
    replace = false,
    restore = false,
  } = {}) {
    const nextSearch = buildLibraryViewSearch({
      currentSearch: view.search,
      category,
      arrange,
      query,
    });
    const nextTarget = joinPathAndSearch("/library", nextSearch, view.hash);
    const currentTarget = joinPathAndSearch(view.pathname, view.search, view.hash);
    if (nextTarget === currentTarget && !view.fromDetail) {
      return false;
    }
    if (restore) {
      markLibraryReturnPending({ userId: user?.id, role: user?.role });
    }
    navigate(
      { pathname: "/library", search: nextSearch, hash: view.hash },
      { replace, state: restore ? { restoreLibraryReturn: true } : undefined },
    );
    return true;
  }

  function closePanel({ returnFocus = true } = {}) {
    const closingPanel = openPanel;
    setOpenPanel("");
    if (!returnFocus) {
      return;
    }
    window.setTimeout(() => {
      if (closingPanel === "arrange") {
        arrangeButtonRef.current?.focus({ preventScroll: true });
      } else if (closingPanel === "avatar") {
        avatarButtonRef.current?.focus({ preventScroll: true });
      }
    }, 0);
  }

  function commitArrangeDraftAndClose({
    category = committedCategory,
    navigateAfter = null,
    returnFocus = true,
  } = {}) {
    if (closingRef.current) {
      return;
    }
    closingRef.current = true;
    const normalizedDraft = normalizeLibraryArrange(draftArrange, libraryState?.availableGenres);
    const changed = !libraryArrangeEquals(normalizedDraft, committedArrange)
      || category !== committedCategory;
    setOpenPanel("");
    if (navigateAfter) {
      const listSearch = buildLibraryViewSearch({
        currentSearch: view.search,
        category,
        arrange: normalizedDraft,
        query: committedQuery,
      });
      libraryState?.prepareExit?.(`/library${listSearch}${view.hash || ""}`);
      navigate(navigateAfter.to, { state: navigateAfter.state });
    } else if (changed) {
      navigateToView({ arrange: normalizedDraft, category });
    }
    if (returnFocus) {
      window.setTimeout(() => arrangeButtonRef.current?.focus({ preventScroll: true }), 0);
    }
    queueMicrotask(() => {
      closingRef.current = false;
    });
  }

  function openArrange() {
    if (openPanel === "arrange") {
      commitArrangeDraftAndClose();
      return;
    }
    setDraftArrange(normalizeLibraryArrange(committedArrange, libraryState?.availableGenres));
    setOpenPanel("arrange");
  }

  function toggleAvatar() {
    if (openPanel === "arrange") {
      commitArrangeDraftAndClose({ returnFocus: false });
      setOpenPanel("avatar");
      return;
    }
    setOpenPanel((current) => (current === "avatar" ? "" : "avatar"));
  }

  function handleCategory(category) {
    if (openPanel === "arrange") {
      commitArrangeDraftAndClose({ category, returnFocus: false });
      return;
    }
    if (
      view.fromDetail
      && category === committedCategory
    ) {
      navigateToView({ restore: true });
      return;
    }
    if (
      category !== committedCategory
      || view.fromDetail
    ) {
      navigateToView({ category });
    }
  }

  function commitSearch(value) {
    navigateToView({ query: String(value || "").trim(), replace: true });
  }

  function handleSearchFocus() {
    if (openPanel === "arrange") {
      commitArrangeDraftAndClose({ returnFocus: false });
    } else if (openPanel === "avatar") {
      closePanel({ returnFocus: false });
    }
  }

  function handleAvatarAction(entry) {
    if (entry.key === "logout") {
      closePanel({ returnFocus: false });
      onLogout();
      return;
    }
    const assistantState = entry.key === "assistant" && user?.role !== "admin"
      ? { fromPath: view.pathname }
      : undefined;
    const listSearch = buildLibraryViewSearch({
      currentSearch: view.search,
      category: committedCategory,
      arrange: committedArrange,
      query: committedQuery,
    });
    libraryState?.prepareExit?.(`/library${listSearch}${view.hash || ""}`);
    closePanel({ returnFocus: false });
    navigate(entry.to, { state: assistantState });
  }

  useEffect(() => {
    if (!openPanel) {
      return undefined;
    }
    function handlePointerDown(event) {
      if (islandRef.current?.contains(event.target)) {
        return;
      }
      if (openPanel === "arrange") {
        commitArrangeDraftAndClose();
      } else {
        closePanel();
      }
    }
    function handleKeyDown(event) {
      if (event.key !== "Escape") {
        return;
      }
      event.preventDefault();
      if (openPanel === "arrange") {
        commitArrangeDraftAndClose();
      } else {
        closePanel();
      }
    }
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [committedArrange, committedCategory, draftArrange, openPanel, view.search]);

  useLayoutEffect(() => {
    const island = islandRef.current;
    const popover = openPanel === "arrange" ? panelRef.current : menuRef.current;
    if (!island) {
      return undefined;
    }
    function updateLayout() {
      const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
      const safeWidth = Math.max(0, viewportWidth - 24);
      const compressed = Math.max(0, 1180 - safeWidth);
      const gap = Math.max(10, 20 - Math.ceil(compressed / 120));
      const tabGap = Math.max(12, 25 - Math.ceil(compressed / 90));
      const collapsed = Math.max(88, Math.min(138, 138 - Math.ceil(compressed / 14)));
      const expanded = Math.max(collapsed, Math.min(250, safeWidth - 650));
      island.style.setProperty("--desktop-island-gap", `${gap}px`);
      island.style.setProperty("--desktop-island-tab-gap", `${tabGap}px`);
      island.style.setProperty("--desktop-island-search-collapsed-width", `${collapsed}px`);
      island.style.setProperty("--desktop-island-search-expanded-width", `${expanded}px`);
      if (!popover) {
        return;
      }
      popover.style.setProperty("--desktop-island-popover-shift-x", "0px");
      const rect = popover.getBoundingClientRect();
      const edge = 12;
      const shift = rect.left < edge
        ? edge - rect.left
        : (rect.right > viewportWidth - edge ? (viewportWidth - edge) - rect.right : 0);
      popover.style.setProperty("--desktop-island-popover-shift-x", `${Math.round(shift)}px`);
      const availableHeight = direction === "down"
        ? window.innerHeight - rect.top - edge
        : rect.bottom - edge;
      popover.style.setProperty(
        "--desktop-island-popover-max-height",
        `${Math.max(220, Math.floor(availableHeight))}px`,
      );
    }
    updateLayout();
    const observer = typeof ResizeObserver === "function"
      ? new ResizeObserver(updateLayout)
      : null;
    observer?.observe(island);
    window.addEventListener("resize", updateLayout);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", updateLayout);
    };
  }, [direction, openPanel]);

  function handleCategoryKeyDown(event, index) {
    let nextIndex = null;
    if (event.key === "ArrowLeft") nextIndex = Math.max(0, index - 1);
    if (event.key === "ArrowRight") nextIndex = Math.min(LIBRARY_CATEGORY_OPTIONS.length - 1, index + 1);
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = LIBRARY_CATEGORY_OPTIONS.length - 1;
    if (nextIndex === null) return;
    event.preventDefault();
    const next = LIBRARY_CATEGORY_OPTIONS[nextIndex];
    islandRef.current?.querySelector(`[data-library-category="${next.key}"]`)?.focus();
    handleCategory(next.key);
  }

  return (
    <div
      className={[
        "desktop-library-island-wrap",
        `desktop-library-island-wrap--${position === "bottom" ? "bottom" : "top"}`,
      ].join(" ")}
      data-testid="desktop-library-island"
      ref={islandRef}
    >
      <nav aria-label="Library controls" className="desktop-library-island">
        <span className="desktop-library-island__brand">Elvern</span>
        <div aria-label="Library category" className="desktop-library-island__tabs" role="tablist">
          {LIBRARY_CATEGORY_OPTIONS.map((category, index) => {
            const active = category.key === committedCategory;
            return (
              <button
                aria-selected={active}
                className={active ? "is-active" : ""}
                data-library-category={category.key}
                key={category.key}
                onClick={() => handleCategory(category.key)}
                onKeyDown={(event) => handleCategoryKeyDown(event, index)}
                role="tab"
                tabIndex={active ? 0 : -1}
                type="button"
              >
                {category.label}
              </button>
            );
          })}
        </div>
        <label className="desktop-library-island__search">
          <span className="sr-only">Search library</span>
          <Search aria-hidden="true" />
          <input
            aria-label="Search library"
            autoComplete="off"
            onChange={(event) => setSearchDraft(event.target.value)}
            onFocus={handleSearchFocus}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.isComposing) {
                event.preventDefault();
                commitSearch(searchDraft);
              } else if (event.key === "Escape" && !event.isComposing) {
                event.preventDefault();
                setSearchDraft(committedQuery);
                searchInputRef.current?.blur();
              }
            }}
            placeholder="Search"
            ref={searchInputRef}
            role="searchbox"
            type="search"
            value={searchDraft}
          />
        </label>
        <button
          aria-controls="desktop-library-arrange-panel"
          aria-expanded={openPanel === "arrange"}
          aria-label="Arrange library"
          className={[
            "desktop-library-island__icon-button",
            openPanel === "arrange" ? "is-open" : "",
          ].join(" ")}
          onClick={openArrange}
          ref={arrangeButtonRef}
          type="button"
        >
          <SlidersHorizontal aria-hidden="true" />
          {badgeCount ? <span className="desktop-library-island__badge">{badgeCount}</span> : null}
        </button>
        <button
          aria-controls="desktop-library-avatar-menu"
          aria-expanded={openPanel === "avatar"}
          aria-label={`Account: ${user?.username || ""}`}
          className={[
            "desktop-library-island__avatar",
            openPanel === "avatar" ? "is-open" : "",
          ].join(" ")}
          onClick={toggleAvatar}
          ref={avatarButtonRef}
          type="button"
        >
          {initial}
        </button>
      </nav>

      {openPanel === "arrange" ? (
        <DesktopLibraryArrangePanel
          availableGenres={libraryState?.availableGenres || []}
          direction={direction}
          draft={draftArrange}
          onDone={() => commitArrangeDraftAndClose()}
          onReset={() => setDraftArrange(DEFAULT_LIBRARY_ARRANGE)}
          onUpdate={(next) => setDraftArrange((current) => normalizeLibraryArrange({
            ...current,
            ...next,
          }, libraryState?.availableGenres))}
          panelRef={panelRef}
        />
      ) : null}
      {openPanel === "avatar" ? (
        <DesktopLibraryAvatarMenu
          direction={direction}
          menuRef={menuRef}
          onAction={handleAvatarAction}
          user={user}
        />
      ) : null}
    </div>
  );
}
