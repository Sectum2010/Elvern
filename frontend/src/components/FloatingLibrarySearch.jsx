import { useEffect, useRef } from "react";

import { shouldCommitLibrarySearchKey } from "../lib/useCommittedLibrarySearch.js";


function isActiveElementInRefs(refs) {
  if (typeof document === "undefined") {
    return false;
  }
  return refs.some((ref) => ref?.current && document.activeElement === ref.current);
}


export function FloatingLibrarySearch({
  enabled = true,
  value,
  onChange,
  placeholder = "Search library",
  label = "Search library",
  mainInputRefs = [],
  desktopInteractionMode = false,
  expanded = false,
  locked = false,
  onClear,
  onCommit,
  onRevert,
  onToggleExpanded,
}) {
  const containerRef = useRef(null);
  const inputRef = useRef(null);
  const composingRef = useRef(false);

  useEffect(() => {
    if (!expanded || isActiveElementInRefs(mainInputRefs)) {
      return undefined;
    }
    const timerId = window.setTimeout(() => {
      inputRef.current?.focus();
    }, 0);
    return () => window.clearTimeout(timerId);
  }, [expanded]);

  if (!enabled) {
    return null;
  }

  function handleCompactClick() {
    if (isActiveElementInRefs(mainInputRefs)) {
      return;
    }
    onToggleExpanded?.();
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }

  function handleSearchButtonClick() {
    if (!expanded) {
      handleCompactClick();
      return;
    }
    onToggleExpanded?.();
  }

  function handleKeyDown(event) {
    if (shouldCommitLibrarySearchKey(event) && !composingRef.current) {
      event.preventDefault();
      onCommit?.("floating");
      return;
    }
    if (event.key !== "Escape" || event.isComposing || composingRef.current) {
      return;
    }
    event.preventDefault();
    onRevert?.("floating");
  }

  return (
    <div
      className={[
        "floating-library-search",
        expanded ? "floating-library-search--expanded" : "floating-library-search--compact",
        desktopInteractionMode ? "floating-library-search--desktop" : "",
      ].join(" ")}
      ref={containerRef}
    >
      {expanded ? (
        <form
          className="floating-library-search__capsule"
          onSubmit={(event) => {
            event.preventDefault();
            onCommit?.("floating");
          }}
        >
          <label className="floating-library-search__field">
            <span className="sr-only">{label}</span>
            <input
              aria-label={label}
              ref={inputRef}
              autoComplete="off"
              disabled={locked}
              inputMode="search"
              onChange={(event) => onChange(event.target.value, {
                action: "input",
                previousValue: value,
                source: "floating",
              })}
              onCompositionEnd={() => {
                composingRef.current = false;
              }}
              onCompositionStart={() => {
                composingRef.current = true;
              }}
              onKeyDown={handleKeyDown}
              placeholder={placeholder}
              role="searchbox"
              type="text"
              value={value}
            />
            {value && !desktopInteractionMode ? (
              <button
                aria-label="Clear search"
                className="floating-library-search__clear"
                onClick={() => {
                  onClear?.("floating");
                  inputRef.current?.focus({ preventScroll: true });
                }}
                onMouseDown={(event) => {
                  event.preventDefault();
                }}
                type="button"
              >
                X
              </button>
            ) : null}
          </label>
          <button
            aria-expanded={expanded}
            aria-label="Collapse search"
            className="floating-library-search__button"
            onClick={handleSearchButtonClick}
            type="button"
          >
            <svg aria-hidden="true" className="floating-library-search__icon" viewBox="0 0 24 24">
              <circle cx="10.5" cy="10.5" r="5.8" fill="none" stroke="currentColor" strokeWidth="2.2" />
              <path d="M15 15l5 5" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="2.2" />
            </svg>
          </button>
        </form>
      ) : (
        <button
          aria-expanded={expanded}
          aria-label={label}
          className="floating-library-search__button"
          onClick={handleSearchButtonClick}
          type="button"
        >
          <svg aria-hidden="true" className="floating-library-search__icon" viewBox="0 0 24 24">
            <circle cx="10.5" cy="10.5" r="5.8" fill="none" stroke="currentColor" strokeWidth="2.2" />
            <path d="M15 15l5 5" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="2.2" />
          </svg>
        </button>
      )}
    </div>
  );
}
