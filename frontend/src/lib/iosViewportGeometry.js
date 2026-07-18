export const IOS_VIEWPORT_GEOMETRY_STORAGE_KEY = "elvern_ios_viewport_geometry_v1";
export const IOS_VIEWPORT_GEOMETRY_MAX_AGE_MS = 24 * 60 * 60 * 1000;
export const IOS_VIEWPORT_GEOMETRY_MAX_RECORDS = 12;
export const IOS_VIEWPORT_WIDTH_BUCKET_PX = 64;

const SCHEMA_VERSION = 1;
const ALLOWED_PLATFORMS = new Set(["iphone", "ipad"]);
const ALLOWED_DISPLAY_MODES = new Set(["standalone", "browser"]);
const ALLOWED_ORIENTATIONS = new Set(["portrait", "landscape"]);
const RECORD_KEYS = Object.freeze([
  "schema_version",
  "platform",
  "display_mode",
  "orientation",
  "width_bucket",
  "screen_width",
  "screen_height",
  "trusted_layout_width",
  "trusted_layout_height",
  "physical_paint_floor_height",
  "updated_at",
]);


function finitePositive(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? Math.round(number) : 0;
}


export function getIOSViewportWidthBucket(width) {
  const normalized = finitePositive(width);
  return normalized ? Math.round(normalized / IOS_VIEWPORT_WIDTH_BUCKET_PX) * IOS_VIEWPORT_WIDTH_BUCKET_PX : 0;
}


function normalizeRecord(candidate) {
  const record = Object.fromEntries(RECORD_KEYS.map((key) => [key, candidate?.[key]]));
  record.schema_version = Number(record.schema_version);
  record.width_bucket = finitePositive(record.width_bucket);
  record.screen_width = finitePositive(record.screen_width);
  record.screen_height = finitePositive(record.screen_height);
  record.trusted_layout_width = finitePositive(record.trusted_layout_width);
  record.trusted_layout_height = finitePositive(record.trusted_layout_height);
  record.physical_paint_floor_height = finitePositive(record.physical_paint_floor_height);
  record.updated_at = Number(record.updated_at);
  if (
    record.schema_version !== SCHEMA_VERSION
    || !ALLOWED_PLATFORMS.has(record.platform)
    || !ALLOWED_DISPLAY_MODES.has(record.display_mode)
    || !ALLOWED_ORIENTATIONS.has(record.orientation)
    || !record.width_bucket
    || !record.screen_width
    || !record.screen_height
    || !record.trusted_layout_width
    || !record.trusted_layout_height
    || !record.physical_paint_floor_height
    || !Number.isFinite(record.updated_at)
    || record.updated_at <= 0
  ) {
    return null;
  }
  return record;
}


function parseRecords(storage) {
  if (!storage?.getItem) return [];
  try {
    const payload = JSON.parse(storage.getItem(IOS_VIEWPORT_GEOMETRY_STORAGE_KEY) || "null");
    if (payload?.schema_version !== SCHEMA_VERSION || !Array.isArray(payload.records)) return [];
    return payload.records.map(normalizeRecord).filter(Boolean);
  } catch {
    return [];
  }
}


function persistRecords(storage, records) {
  if (!storage?.setItem) return;
  try {
    storage.setItem(IOS_VIEWPORT_GEOMETRY_STORAGE_KEY, JSON.stringify({
      schema_version: SCHEMA_VERSION,
      records: records.slice(0, IOS_VIEWPORT_GEOMETRY_MAX_RECORDS),
    }));
  } catch {
    // Geometry is optional and must not block startup in restricted storage modes.
  }
}


function screenMatches(record, screenWidth, screenHeight) {
  return Math.abs(record.screen_width - finitePositive(screenWidth)) < IOS_VIEWPORT_WIDTH_BUCKET_PX
    && Math.abs(record.screen_height - finitePositive(screenHeight)) < IOS_VIEWPORT_WIDTH_BUCKET_PX;
}


export function readMatchingIOSViewportGeometry({
  storage,
  now = Date.now(),
  platform,
  displayMode,
  orientation,
  layoutWidth,
  screenWidth,
  screenHeight,
}) {
  const currentTime = Number(now);
  const records = parseRecords(storage);
  const fresh = records
    .filter((record) => currentTime >= record.updated_at && currentTime - record.updated_at <= IOS_VIEWPORT_GEOMETRY_MAX_AGE_MS)
    .sort((left, right) => right.updated_at - left.updated_at);
  if (fresh.length !== records.length) persistRecords(storage, fresh);
  const widthBucket = getIOSViewportWidthBucket(layoutWidth);
  return fresh.find((record) => (
    record.platform === platform
    && record.display_mode === displayMode
    && record.orientation === orientation
    && record.width_bucket === widthBucket
    && screenMatches(record, screenWidth, screenHeight)
  )) || null;
}


export function writeIOSViewportGeometry({ storage, record }) {
  const normalized = normalizeRecord(record);
  if (!normalized) return false;
  const records = parseRecords(storage).filter((candidate) => !(
    candidate.platform === normalized.platform
    && candidate.display_mode === normalized.display_mode
    && candidate.orientation === normalized.orientation
    && candidate.width_bucket === normalized.width_bucket
  ));
  records.unshift(normalized);
  records.sort((left, right) => right.updated_at - left.updated_at);
  persistRecords(storage, records);
  return true;
}
