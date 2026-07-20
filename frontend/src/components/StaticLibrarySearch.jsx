import { forwardRef } from "react";

import { shouldCommitLibrarySearchKey } from "../lib/useCommittedLibrarySearch.js";


export const StaticLibrarySearch = forwardRef(function StaticLibrarySearch({
  fieldClassName = "",
  formClassName = "",
  label = "Search library",
  placeholder = "Search title or filename",
  search,
}, ref) {
  return (
    <form
      className={["library-search-form", formClassName].filter(Boolean).join(" ")}
      onSubmit={(event) => {
        event.preventDefault();
        search.commit("static");
      }}
    >
      <label className={["search-field", fieldClassName].filter(Boolean).join(" ")}>
        <span className="sr-only">{label}</span>
        <input
          aria-label={label}
          disabled={search.isSourceLocked("static")}
          inputMode="search"
          onChange={(event) => search.updateDraft("static", event.target.value)}
          onKeyDown={(event) => {
            if (shouldCommitLibrarySearchKey(event)) {
              event.preventDefault();
              search.commit("static");
            } else if (event.key === "Escape" && !event.isComposing) {
              event.preventDefault();
              search.revert("static");
            }
          }}
          placeholder={placeholder}
          ref={ref}
          role="searchbox"
          type="text"
          value={search.staticDraft}
        />
      </label>
    </form>
  );
});
