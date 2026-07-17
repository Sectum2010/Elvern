import { isCompleteLibraryQualityRank } from "./qualityRank.js";


export const LIBRARY_SUMMARY_V2_SCHEMA_VERSION = "library-summary-v2";
export const LIBRARY_SUMMARY_V2_MODE_OFF = "off";
export const LIBRARY_SUMMARY_V2_MODE_SHADOW = "shadow";
export const LIBRARY_SUMMARY_V2_MODE_ON = "on";
export const LIBRARY_SUMMARY_V2_DEBUG_STORAGE_KEY = "elvern_library_summary_v2_debug";

const VALID_MODES = new Set([
  LIBRARY_SUMMARY_V2_MODE_OFF,
  LIBRARY_SUMMARY_V2_MODE_SHADOW,
  LIBRARY_SUMMARY_V2_MODE_ON,
]);
const REQUIRED_ITEM_FIELDS = Object.freeze([
  "id",
  "title",
  "year",
  "poster_url",
  "source_kind",
  "quality_rank",
  "duration_seconds",
  "progress_seconds",
  "progress_duration_seconds",
  "completed",
]);
const REQUIRED_TOP_LEVEL_FIELDS = Object.freeze([
  "schema_version",
  "revision",
  "view",
  "items_by_id",
  "sections",
  "available_genres",
  "total_items",
  "scan_in_progress",
]);
const REQUIRED_VIEW_FIELDS = Object.freeze(["category", "source", "genre", "quality", "sort"]);
const REQUIRED_QUALITY_FIELDS = Object.freeze([
  "key",
  "label",
  "score",
  "description",
  "detected",
  "tooltip",
]);
const REQUIRED_SECTION_FIELDS = Object.freeze([
  "item_ids",
  "series_rails",
  "cloud_series_rails",
  "continue_watching_item_ids",
  "recently_added_item_ids",
]);
const REQUIRED_RAIL_FIELDS = Object.freeze(["key", "title", "film_count", "item_ids"]);


export class LibrarySummaryV2ContractError extends Error {
  constructor(message, details = {}) {
    super(message);
    this.name = "LibrarySummaryV2ContractError";
    this.code = "library_summary_v2_contract_error";
    this.details = details;
  }
}


export function resolveLibrarySummaryV2Mode(
  rawMode = import.meta.env.VITE_ELVERN_LIBRARY_SUMMARY_V2_MODE,
) {
  const normalized = String(rawMode ?? "").trim().toLowerCase();
  if (!normalized) {
    return LIBRARY_SUMMARY_V2_MODE_ON;
  }
  return VALID_MODES.has(normalized) ? normalized : LIBRARY_SUMMARY_V2_MODE_OFF;
}


export function isLibrarySummaryV2DebugEnabled(storage = globalThis?.localStorage) {
  try {
    return ["1", "true", "yes", "on"].includes(
      String(storage?.getItem?.(LIBRARY_SUMMARY_V2_DEBUG_STORAGE_KEY) || "").trim().toLowerCase(),
    );
  } catch {
    return false;
  }
}


export function buildLibrarySummaryV2RequestPath({
  category = "movies",
  source = "all",
  genre = "",
  quality = "all",
  sort = "smart",
} = {}) {
  const params = new URLSearchParams();
  params.set("category", String(category || "movies").trim().toLowerCase() || "movies");
  const normalizedSource = String(source || "all").trim().toLowerCase() || "all";
  const normalizedGenre = String(genre || "").trim();
  const normalizedQuality = String(quality || "all").trim().toLowerCase() || "all";
  const normalizedSort = String(sort || "smart").trim().toLowerCase() || "smart";
  if (normalizedSource !== "all") {
    params.set("source", normalizedSource);
  }
  if (normalizedGenre) {
    params.set("genre", normalizedGenre);
  }
  if (normalizedQuality !== "all") {
    params.set("quality", normalizedQuality);
  }
  if (normalizedSort !== "smart") {
    params.set("sort", normalizedSort);
  }
  return `/api/library/v2/summary?${params.toString()}`;
}


function requireObject(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new LibrarySummaryV2ContractError(`${label} must be an object`, { category: label });
  }
  return value;
}


function requireExactFields(value, expectedFields, label, details = {}) {
  const actualFields = Object.keys(value).sort();
  const expected = [...expectedFields].sort();
  if (JSON.stringify(actualFields) !== JSON.stringify(expected)) {
    throw new LibrarySummaryV2ContractError(`${label} has unsupported fields`, {
      category: label,
      ...details,
    });
  }
}


function requireIdArray(value, label, itemsById) {
  if (!Array.isArray(value)) {
    throw new LibrarySummaryV2ContractError(`${label} must be an array`, { category: label });
  }
  value.forEach((itemId) => {
    const normalizedId = Number(itemId);
    if (!Number.isInteger(normalizedId) || !itemsById[String(normalizedId)]) {
      throw new LibrarySummaryV2ContractError(`${label} contains a dangling item ID`, {
        category: label,
        itemId: Number.isFinite(normalizedId) ? normalizedId : null,
      });
    }
  });
  return value;
}


function validateRailCollection(value, label, itemsById) {
  if (!Array.isArray(value)) {
    throw new LibrarySummaryV2ContractError(`${label} must be an array`, { category: label });
  }
  value.forEach((rail) => {
    requireObject(rail, `${label}.rail`);
    requireExactFields(rail, REQUIRED_RAIL_FIELDS, `${label}.rail`);
    requireIdArray(rail.item_ids, `${label}.item_ids`, itemsById);
    if (Number(rail.film_count) !== rail.item_ids.length) {
      throw new LibrarySummaryV2ContractError(`${label} film_count does not match item_ids`, {
        category: label,
      });
    }
  });
  return value;
}


export function validateLibrarySummaryV2Payload(payload) {
  requireObject(payload, "payload");
  requireExactFields(payload, REQUIRED_TOP_LEVEL_FIELDS, "payload");
  if (payload.schema_version !== LIBRARY_SUMMARY_V2_SCHEMA_VERSION) {
    throw new LibrarySummaryV2ContractError("Unsupported library summary schema version", {
      category: "schema_version",
    });
  }
  if (typeof payload.revision !== "string" || !/^[a-f0-9]{64}$/.test(payload.revision)) {
    throw new LibrarySummaryV2ContractError("Library summary revision must be an opaque SHA-256 identity", {
      category: "revision",
    });
  }
  requireObject(payload.view, "view");
  requireExactFields(payload.view, REQUIRED_VIEW_FIELDS, "view");
  const itemsById = requireObject(payload.items_by_id, "items_by_id");
  Object.entries(itemsById).forEach(([mapId, item]) => {
    requireObject(item, "item");
    if (String(item.id) !== mapId) {
      throw new LibrarySummaryV2ContractError("items_by_id key does not match entity ID", {
        category: "items_by_id",
        itemId: Number(item.id) || null,
      });
    }
    REQUIRED_ITEM_FIELDS.forEach((fieldName) => {
      if (!(fieldName in item)) {
        throw new LibrarySummaryV2ContractError(`v2 item is missing ${fieldName}`, {
          category: "item_field",
          itemId: Number(item.id) || null,
        });
      }
    });
    requireExactFields(item, REQUIRED_ITEM_FIELDS, "item_field", {
      itemId: Number(item.id) || null,
    });
    requireObject(item.quality_rank, "quality_rank");
    requireExactFields(item.quality_rank, REQUIRED_QUALITY_FIELDS, "quality_rank", {
      itemId: Number(item.id) || null,
    });
    if (!isCompleteLibraryQualityRank(item.quality_rank)) {
      throw new LibrarySummaryV2ContractError("quality_rank has invalid field values", {
        category: "quality_rank",
        itemId: Number(item.id) || null,
      });
    }
  });
  const sections = requireObject(payload.sections, "sections");
  requireExactFields(sections, REQUIRED_SECTION_FIELDS, "sections");
  requireIdArray(sections.item_ids, "sections.item_ids", itemsById);
  validateRailCollection(sections.series_rails, "sections.series_rails", itemsById);
  validateRailCollection(sections.cloud_series_rails, "sections.cloud_series_rails", itemsById);
  requireIdArray(
    sections.continue_watching_item_ids,
    "sections.continue_watching_item_ids",
    itemsById,
  );
  requireIdArray(
    sections.recently_added_item_ids,
    "sections.recently_added_item_ids",
    itemsById,
  );
  return payload;
}


function resolveItems(itemIds, itemsById) {
  return itemIds.map((itemId) => itemsById[String(itemId)]);
}


function adaptRails(rails, itemsById) {
  return rails.map((rail) => ({
    key: rail.key,
    title: rail.title,
    film_count: rail.film_count,
    items: resolveItems(rail.item_ids, itemsById),
  }));
}


export function adaptLibrarySummaryV2ToLegacyView(payload) {
  const validated = validateLibrarySummaryV2Payload(payload);
  const { items_by_id: itemsById, sections } = validated;
  return {
    items: resolveItems(sections.item_ids, itemsById),
    series_rails: adaptRails(sections.series_rails, itemsById),
    cloud_series_rails: adaptRails(sections.cloud_series_rails, itemsById),
    continue_watching: resolveItems(sections.continue_watching_item_ids, itemsById),
    recently_added: resolveItems(sections.recently_added_item_ids, itemsById),
    arrange: {
      source: validated.view.source,
      genre: validated.view.genre,
      quality: validated.view.quality,
      sort: validated.view.sort,
    },
    available_genres: validated.available_genres,
    total_items: validated.total_items,
    scan_in_progress: validated.scan_in_progress,
    revision: validated.revision,
  };
}


function itemIds(items) {
  return (items || []).map((item) => Number(item?.id));
}


function sameValue(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}


function hashedRailKey(value) {
  let hash = 2166136261;
  for (const character of String(value || "")) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}


function v1ItemsById(payload) {
  const map = new Map();
  const addItems = (items) => (items || []).forEach((item) => {
    const itemId = Number(item?.id);
    if (Number.isInteger(itemId) && !map.has(itemId)) {
      map.set(itemId, item);
    }
  });
  addItems(payload?.items);
  (payload?.series_rails || []).forEach((rail) => addItems(rail?.items));
  (payload?.cloud_series_rails || []).forEach((rail) => addItems(rail?.items));
  addItems(payload?.recently_added);
  (payload?.continue_watching || []).forEach((item) => {
    const itemId = Number(item?.id);
    if (!Number.isInteger(itemId)) {
      return;
    }
    const current = map.get(itemId) || item;
    map.set(itemId, {
      ...current,
      progress_seconds: item.progress_seconds,
      progress_duration_seconds: item.progress_duration_seconds,
      completed: item.completed,
    });
  });
  return map;
}


export function compareLibraryV1AndV2(v1Payload, v2Payload, { viewIdentity = {} } = {}) {
  const mismatches = [];
  const record = (category, details = {}) => {
    mismatches.push({
      category,
      ...(Number.isInteger(Number(details.itemId)) ? { itemId: Number(details.itemId) } : {}),
      ...(details.section ? { section: details.section } : {}),
      ...(details.railKey ? { railKeyHash: hashedRailKey(details.railKey) } : {}),
    });
  };
  let validated;
  try {
    validated = validateLibrarySummaryV2Payload(v2Payload);
  } catch (error) {
    record(error?.details?.category || "contract", { itemId: error?.details?.itemId });
    return { matches: false, mismatchCount: mismatches.length, mismatches };
  }
  const expectedView = {
    category: String(viewIdentity.category || "movies"),
    source: String(v1Payload?.arrange?.source || "all"),
    genre: v1Payload?.arrange?.genre ?? null,
    quality: String(v1Payload?.arrange?.quality || "all"),
    sort: String(v1Payload?.arrange?.sort || "smart"),
  };
  if (!sameValue(expectedView, validated.view)) record("view");
  if (Number(v1Payload?.total_items || 0) !== Number(validated.total_items || 0)) record("total_items");
  if (!sameValue(v1Payload?.available_genres || [], validated.available_genres || [])) record("available_genres");
  if (Boolean(v1Payload?.scan_in_progress) !== Boolean(validated.scan_in_progress)) record("scan_in_progress");
  if (!sameValue(itemIds(v1Payload?.items), validated.sections.item_ids)) record("item_order", { section: "items" });
  if (!sameValue(itemIds(v1Payload?.continue_watching), validated.sections.continue_watching_item_ids)) {
    record("item_order", { section: "continue_watching" });
  }
  if (!sameValue(itemIds(v1Payload?.recently_added), validated.sections.recently_added_item_ids)) {
    record("item_order", { section: "recently_added" });
  }
  for (const sectionName of ["series_rails", "cloud_series_rails"]) {
    const v1Rails = v1Payload?.[sectionName] || [];
    const v2Rails = validated.sections[sectionName] || [];
    if (v1Rails.length !== v2Rails.length) {
      record("rail_count", { section: sectionName });
    }
    v1Rails.forEach((v1Rail, index) => {
      const v2Rail = v2Rails[index];
      if (!v2Rail) return;
      if (
        v1Rail.key !== v2Rail.key
        || v1Rail.title !== v2Rail.title
        || Number(v1Rail.film_count) !== Number(v2Rail.film_count)
        || !sameValue(itemIds(v1Rail.items), v2Rail.item_ids)
      ) {
        record("rail_membership", { section: sectionName, railKey: v1Rail.key });
      }
    });
  }
  const v1Map = v1ItemsById(v1Payload);
  const v2Map = validated.items_by_id;
  if (!sameValue([...v1Map.keys()].sort((a, b) => a - b), Object.keys(v2Map).map(Number).sort((a, b) => a - b))) {
    record("item_membership");
  }
  v1Map.forEach((v1Item, itemId) => {
    const v2Item = v2Map[String(itemId)];
    if (!v2Item) return;
    const fieldPairs = [
      ["title", String(v1Item.title || ""), String(v2Item.title || "")],
      ["year", v1Item.year ?? null, v2Item.year ?? null],
      ["poster_url", v1Item.poster_url ?? null, v2Item.poster_url ?? null],
      ["source_kind", v1Item.source_kind || "local", v2Item.source_kind || "local"],
      ["duration", v1Item.duration_seconds ?? null, v2Item.duration_seconds ?? null],
      ["progress", v1Item.progress_seconds ?? null, v2Item.progress_seconds ?? null],
      ["progress_duration", v1Item.progress_duration_seconds ?? null, v2Item.progress_duration_seconds ?? null],
      ["completed", Boolean(v1Item.completed), Boolean(v2Item.completed)],
    ];
    fieldPairs.forEach(([category, left, right]) => {
      if (!sameValue(left, right)) record(category, { itemId });
    });
    if (!isCompleteLibraryQualityRank(v1Item.quality_rank)) {
      record("v1_quality_rank_missing", { itemId });
    } else if (!sameValue(v1Item.quality_rank, v2Item.quality_rank)) {
      record("quality_rank", { itemId });
    }
  });
  return {
    matches: mismatches.length === 0,
    mismatchCount: mismatches.length,
    mismatches,
  };
}


export function isLibrarySummaryV2CapabilityFailure(error) {
  if (error instanceof LibrarySummaryV2ContractError) {
    return true;
  }
  if (Number(error?.status) === 404) {
    return true;
  }
  const detail = error?.detail || error?.payload?.detail;
  return detail?.code === "library_summary_v2_disabled";
}
