import { describe, expect, test, vi } from "vitest";

import {
  IOS_VIEWPORT_GEOMETRY_MAX_AGE_MS,
  IOS_VIEWPORT_GEOMETRY_STORAGE_KEY,
  getIOSViewportWidthBucket,
  readMatchingIOSViewportGeometry,
  writeIOSViewportGeometry,
} from "./iosViewportGeometry.js";


function createStorage(initial = null) {
  let value = initial;
  return {
    getItem: vi.fn(() => value),
    removeItem: vi.fn(() => { value = null; }),
    setItem: vi.fn((key, next) => { value = next; }),
    value: () => value,
  };
}


function geometry(overrides = {}) {
  return {
    schema_version: 1,
    platform: "iphone",
    display_mode: "standalone",
    orientation: "portrait",
    width_bucket: getIOSViewportWidthBucket(390),
    screen_width: 390,
    screen_height: 844,
    trusted_layout_width: 390,
    trusted_layout_height: 844,
    physical_paint_floor_height: 844,
    updated_at: 1_000_000,
    ...overrides,
  };
}


describe("iOS trusted viewport geometry store", () => {
  test("uses a matching 23 hour record and rejects an expired 25 hour record", () => {
    const current = geometry();
    const storage = createStorage(JSON.stringify({ schema_version: 1, records: [current] }));

    expect(readMatchingIOSViewportGeometry({
      storage,
      now: current.updated_at + (23 * 60 * 60 * 1000),
      platform: "iphone",
      displayMode: "standalone",
      orientation: "portrait",
      layoutWidth: 390,
      screenWidth: 390,
      screenHeight: 844,
    })).toMatchObject({ trusted_layout_height: 844 });

    expect(readMatchingIOSViewportGeometry({
      storage,
      now: current.updated_at + (25 * 60 * 60 * 1000),
      platform: "iphone",
      displayMode: "standalone",
      orientation: "portrait",
      layoutWidth: 390,
      screenWidth: 390,
      screenHeight: 844,
    })).toBeNull();
    expect(storage.setItem).toHaveBeenCalledWith(IOS_VIEWPORT_GEOMETRY_STORAGE_KEY, expect.any(String));
    expect(IOS_VIEWPORT_GEOMETRY_MAX_AGE_MS).toBe(24 * 60 * 60 * 1000);
  });

  test.each([
    ["width bucket", { layoutWidth: 700 }],
    ["display mode", { displayMode: "browser" }],
    ["orientation", { orientation: "landscape" }],
  ])("does not reuse geometry across a mismatched %s", (_label, override) => {
    const current = geometry();
    const storage = createStorage(JSON.stringify({ schema_version: 1, records: [current] }));

    expect(readMatchingIOSViewportGeometry({
      storage,
      now: current.updated_at + 1,
      platform: "iphone",
      displayMode: "standalone",
      orientation: "portrait",
      layoutWidth: 390,
      screenWidth: 390,
      screenHeight: 844,
      ...override,
    })).toBeNull();
  });

  test("stores only the allowlisted geometry schema and caps records at twelve", () => {
    const storage = createStorage();
    for (let index = 0; index < 15; index += 1) {
      writeIOSViewportGeometry({
        storage,
        record: geometry({
          width_bucket: getIOSViewportWidthBucket(320 + (index * 64)),
          trusted_layout_width: 320 + (index * 64),
          updated_at: 1_000_000 + index,
          pathname: "/library/private",
          username: "private-user",
        }),
      });
    }

    const payload = JSON.parse(storage.value());
    expect(payload.records).toHaveLength(12);
    expect(payload.records[0]).not.toHaveProperty("pathname");
    expect(payload.records[0]).not.toHaveProperty("username");
  });
});
