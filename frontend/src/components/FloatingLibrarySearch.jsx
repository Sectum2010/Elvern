import { useEffect, useRef, useState } from "react";


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
}) {
  const [expanded, setExpanded] = useState(false);
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
    setExpanded(true);
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }

  function handleSearchButtonClick() {
    if (!expanded) {
      handleCompactClick();
      return;
    }
    if (!value) {
      setExpanded(false);
      return;
    }
    inputRef.current?.focus();
  }

  function handleKeyDown(event) {
    if (event.key !== "Escape" || event.isComposing || composingRef.current) {
      return;
    }
    event.preventDefault();
    if (value) {
      onChange("");
      return;
    }
    setExpanded(false);
  }

  function handleBlur(event) {
    const nextTarget = event.relatedTarget;
    if (nextTarget && event.currentTarget.contains(nextTarget)) {
      return;
    }
    window.setTimeout(() => {
      if (containerRef.current?.contains(document.activeElement)) {
        return;
      }
      if (!value && !composingRef.current) {
        setExpanded(false);
      }
    }, 0);
  }

  return (
    <div
      className={[
        "floating-library-search",
        expanded ? "floating-library-search--expanded" : "floating-library-search--compact",
      ].join(" ")}
      onBlur={expanded ? handleBlur : undefined}
      ref={containerRef}
    >
      {expanded ? (
        <form
          className="floating-library-search__capsule"
          onSubmit={(event) => event.preventDefault()}
        >
          <label className="floating-library-search__field">
            <span className="sr-only">{label}</span>
            <input
              ref={inputRef}
              autoComplete="off"
              onChange={(event) => onChange(event.target.value)}
              onCompositionEnd={() => {
                composingRef.current = false;
              }}
              onCompositionStart={() => {
                composingRef.current = true;
              }}
              onKeyDown={handleKeyDown}
              placeholder={placeholder}
              type="search"
              value={value}
            />
            {value ? (
              <button
                aria-label="Clear search"
                className="floating-library-search__clear"
                onClick={() => {
                  onChange("");
                  inputRef.current?.focus();
                }}
                type="button"
              >
                X
              </button>
            ) : null}
          </label>
          <button
            aria-expanded={expanded}
            aria-label={value ? label : "Collapse search"}
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
