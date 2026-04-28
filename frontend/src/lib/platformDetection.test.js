import assert from "node:assert/strict";
import test from "node:test";

import {
  detectClientPlatform,
  detectDesktopPlatform,
  isIOSLikeBrowser,
} from "./platformDetection.js";

test("iPadOS Safari desktop-class user agent is treated as iPad before macOS", () => {
  const input = {
    userAgent:
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      + "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    platform: "MacIntel",
    maxTouchPoints: 5,
  };

  assert.equal(detectClientPlatform(input), "ipad");
  assert.equal(detectDesktopPlatform(input), null);
  assert.equal(isIOSLikeBrowser(input), true);
});

test("real macOS desktop remains a desktop Mac", () => {
  const input = {
    userAgent:
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      + "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    platform: "MacIntel",
    maxTouchPoints: 0,
  };

  assert.equal(detectClientPlatform(input), "mac");
  assert.equal(detectDesktopPlatform(input), "mac");
  assert.equal(isIOSLikeBrowser(input), false);
});
