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
  const inputRef = useRef(null);

  useEffect(() => {
    if (!expanded || isActiveElementInRefs(mainInputRefs)) {
      return undefined;
    }
    const timerId = window.setTimeout(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    }, 0);
    return () => window.clearTimeout(timerId);
  }, [expanded, mainInputRefs]);

  if (!enabled) {
    return null;
  }

  function handleCompactClick() {
    if (isActiveElementInRefs(mainInputRefs)) {
      return;
    }
    setExpanded(true);
  }

  function handleKeyDown(event) {
    if (event.key !== "Escape") {
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
    if (event.currentTarget.contains(nextTarget)) {
      return;
    }
    if (!value) {
      setExpanded(false);
    }
  }

  if (!expanded) {
    return (
      <button
        aria-label={label}
        className="floating-library-search floating-library-search--compact"
        onClick={handleCompactClick}
        type="button"
      >
        <svg aria-hidden="true" className="floating-library-search__icon" viewBox="0 0 24 24">
          <circle cx="10.5" cy="10.5" r="5.8" fill="none" stroke="currentColor" strokeWidth="2.2" />
          <path d="M15 15l5 5" fill="none" stroke="currentColor" strokeLinecap="round" strokeWidth="2.2" />
        </svg>
      </button>
    );
  }

  return (
    <form
      className="floating-library-search floating-library-search--expanded"
      onBlur={handleBlur}
      onSubmit={(event) => event.preventDefault()}
    >
      <label className="floating-library-search__field">
        <span className="sr-only">{label}</span>
        <input
          ref={inputRef}
          autoComplete="off"
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          type="search"
          value={value}
        />
      </label>
      {value ? (
        <button
          aria-label="Clear search"
          className="floating-library-search__action"
          onClick={() => onChange("")}
          type="button"
        >
          X
        </button>
      ) : null}
      <button
        aria-label="Close search"
        className="floating-library-search__action"
        onClick={() => setExpanded(false)}
        type="button"
      >
        Done
      </button>
    </form>
  );
}
