export const LIBRARY_CATEGORY_OPTIONS = Object.freeze([
  { key: "movies", label: "Movies", otherHeading: "Other Movies" },
  { key: "tv", label: "TV Shows", otherHeading: "Other TV Shows" },
  { key: "anime", label: "Anime", otherHeading: "Other Anime" },
  { key: "cartoon", label: "Cartoon", otherHeading: "Other Cartoon" },
]);

export const LIBRARY_SOURCE_OPTIONS = Object.freeze([
  { key: "all", label: "All" },
  { key: "local", label: "Local" },
  { key: "cloud", label: "Cloud" },
]);

export const LIBRARY_QUALITY_OPTIONS = Object.freeze([
  { key: "diamond", label: "Diamond" },
  { key: "gold", label: "Gold" },
  { key: "silver", label: "Silver" },
  { key: "iron", label: "Iron" },
  { key: "bronze", label: "Bronze" },
  { key: "wood", label: "Wood" },
]);

export const LIBRARY_QUALITY_ORDER = Object.freeze(
  LIBRARY_QUALITY_OPTIONS.map((option) => option.key),
);

export const LIBRARY_SORT_OPTIONS = Object.freeze([
  {
    key: "smart",
    label: "Smart Default",
    family: "smart",
    directionLabel: "",
  },
  {
    key: "az",
    alternateKey: "za",
    label: "Alphabetical",
    family: "alphabetical",
    directionLabel: "A → Z",
    alternateDirectionLabel: "Z → A",
  },
  {
    key: "recent_desc",
    alternateKey: "recent_asc",
    label: "Recently added",
    family: "recent",
    directionLabel: "newest ↓",
    alternateDirectionLabel: "oldest ↑",
  },
  {
    key: "year_desc",
    alternateKey: "year_asc",
    label: "Release year",
    family: "year",
    directionLabel: "newest ↓",
    alternateDirectionLabel: "oldest ↑",
  },
  {
    key: "size_desc",
    alternateKey: "size_asc",
    label: "File size",
    family: "size",
    directionLabel: "largest ↓",
    alternateDirectionLabel: "smallest ↑",
  },
]);

export const DEFAULT_LIBRARY_CATEGORY = "movies";
export const DEFAULT_LIBRARY_ARRANGE = Object.freeze({
  source: "all",
  genres: Object.freeze([]),
  qualities: Object.freeze([]),
  sort: "smart",
});

const CATEGORY_KEYS = new Set(LIBRARY_CATEGORY_OPTIONS.map((option) => option.key));
const SOURCE_KEYS = new Set(LIBRARY_SOURCE_OPTIONS.map((option) => option.key));
const QUALITY_KEYS = new Set(LIBRARY_QUALITY_ORDER);
const SORT_KEYS = new Set(
  LIBRARY_SORT_OPTIONS.flatMap((option) => [option.key, option.alternateKey]).filter(Boolean),
);

function normalizeGenreValue(value) {
  return String(value ?? "").trim().replace(/\s+/g, " ");
}

export function normalizeLibraryGenres(values = [], availableGenres = []) {
  const canonicalByKey = new Map(
    (availableGenres || [])
      .map((value) => normalizeGenreValue(value))
      .filter(Boolean)
      .map((value) => [value.toLocaleLowerCase(), value]),
  );
  const normalizedByKey = new Map();
  const candidates = Array.isArray(values) ? values : [values];
  candidates.forEach((candidate) => {
    const normalized = normalizeGenreValue(candidate);
    if (!normalized) {
      return;
    }
    const key = normalized.toLocaleLowerCase();
    normalizedByKey.set(key, canonicalByKey.get(key) || normalized);
  });
  return [...normalizedByKey.values()].sort((left, right) => (
    left.localeCompare(right, undefined, { sensitivity: "base" })
  ));
}

export function normalizeLibraryQualities(values = []) {
  const selected = new Set(
    (Array.isArray(values) ? values : [values])
      .map((value) => String(value ?? "").trim().toLowerCase())
      .filter((value) => QUALITY_KEYS.has(value)),
  );
  return LIBRARY_QUALITY_ORDER.filter((quality) => selected.has(quality));
}

export function normalizeLibraryArrange(arrange = {}, availableGenres = []) {
  const source = String(arrange.source ?? "").trim().toLowerCase();
  const sort = String(arrange.sort ?? "").trim().toLowerCase();
  const legacyGenre = arrange.genre ? [arrange.genre] : [];
  const legacyQuality = arrange.quality && arrange.quality !== "all" ? [arrange.quality] : [];
  return {
    source: SOURCE_KEYS.has(source) ? source : DEFAULT_LIBRARY_ARRANGE.source,
    genres: normalizeLibraryGenres(
      Array.isArray(arrange.genres) ? arrange.genres : legacyGenre,
      availableGenres,
    ),
    qualities: normalizeLibraryQualities(
      Array.isArray(arrange.qualities) ? arrange.qualities : legacyQuality,
    ),
    sort: SORT_KEYS.has(sort) ? sort : DEFAULT_LIBRARY_ARRANGE.sort,
  };
}

export function resolveLibraryCategoryFromSearch(search = "") {
  const value = String(new URLSearchParams(search).get("category") || "").trim().toLowerCase();
  return CATEGORY_KEYS.has(value) ? value : DEFAULT_LIBRARY_CATEGORY;
}

export function resolveLibraryArrangeFromSearch(search = "", availableGenres = []) {
  const params = new URLSearchParams(search);
  return normalizeLibraryArrange({
    source: params.get("source"),
    genres: params.getAll("genre"),
    qualities: params.getAll("quality"),
    sort: params.get("sort"),
  }, availableGenres);
}

export function resolveLibraryQueryFromSearch(search = "") {
  return String(new URLSearchParams(search).get("q") || "").trim();
}

export function applyLibraryArrangeParams(params, arrange = DEFAULT_LIBRARY_ARRANGE) {
  const normalized = normalizeLibraryArrange(arrange);
  if (normalized.source === DEFAULT_LIBRARY_ARRANGE.source) {
    params.delete("source");
  } else {
    params.set("source", normalized.source);
  }
  params.delete("genre");
  normalized.genres.forEach((genre) => params.append("genre", genre));
  params.delete("quality");
  normalized.qualities.forEach((quality) => params.append("quality", quality));
  if (normalized.sort === DEFAULT_LIBRARY_ARRANGE.sort) {
    params.delete("sort");
  } else {
    params.set("sort", normalized.sort);
  }
  return params;
}

export function buildLibraryViewSearch({
  currentSearch = "",
  category,
  arrange,
  query,
} = {}) {
  const params = new URLSearchParams(currentSearch);
  if (category !== undefined) {
    const normalizedCategory = String(category || "").trim().toLowerCase();
    params.set(
      "category",
      CATEGORY_KEYS.has(normalizedCategory) ? normalizedCategory : DEFAULT_LIBRARY_CATEGORY,
    );
  }
  if (arrange !== undefined) {
    applyLibraryArrangeParams(params, arrange);
  }
  if (query !== undefined) {
    const normalizedQuery = String(query || "").trim();
    if (normalizedQuery) {
      params.set("q", normalizedQuery);
    } else {
      params.delete("q");
    }
  }
  const serialized = params.toString();
  return serialized ? `?${serialized}` : "";
}

export function buildLibraryRequestPath({
  category = DEFAULT_LIBRARY_CATEGORY,
  query = "",
  arrange = DEFAULT_LIBRARY_ARRANGE,
} = {}) {
  const search = buildLibraryViewSearch({
    category,
    arrange,
    query,
  });
  return String(query || "").trim()
    ? `/api/library/search${search}`
    : `/api/library${search}`;
}

export function countLibraryArrangeFilters(arrange = DEFAULT_LIBRARY_ARRANGE) {
  const normalized = normalizeLibraryArrange(arrange);
  return (
    (normalized.source !== "all" ? 1 : 0)
    + normalized.genres.length
    + normalized.qualities.length
    + (normalized.sort !== "smart" ? 1 : 0)
  );
}

export function libraryArrangeEquals(left, right) {
  const comparable = (value) => {
    const normalized = normalizeLibraryArrange(value);
    return {
      ...normalized,
      genres: normalized.genres.map((genre) => genre.toLocaleLowerCase()),
    };
  };
  return JSON.stringify(comparable(left)) === JSON.stringify(comparable(right));
}

export function toggleLibrarySort(currentSort, option) {
  if (!option || option.key === "smart") {
    return "smart";
  }
  if (currentSort === option.key && option.alternateKey) {
    return option.alternateKey;
  }
  if (currentSort === option.alternateKey) {
    return option.key;
  }
  return option.key;
}

export function librarySortDirectionLabel(sort) {
  for (const option of LIBRARY_SORT_OPTIONS) {
    if (sort === option.alternateKey) {
      return option.alternateDirectionLabel;
    }
    if (sort === option.key) {
      return option.directionLabel;
    }
  }
  return "";
}

export function buildLegacySourceRedirectLocation(location, source) {
  const params = new URLSearchParams(location?.search || "");
  params.set("source", source === "cloud" ? "cloud" : "local");
  const serialized = params.toString();
  return {
    pathname: "/library",
    search: serialized ? `?${serialized}` : "",
    hash: location?.hash || "",
  };
}
