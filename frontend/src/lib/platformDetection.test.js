import assert from "node:assert/strict";
import test from "node:test";

import {
  detectClientDeviceClass,
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
  assert.equal(detectClientDeviceClass(input), "tablet");
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
  assert.equal(detectClientDeviceClass(input), "desktop");
  assert.equal(detectDesktopPlatform(input), "mac");
  assert.equal(isIOSLikeBrowser(input), false);
});

test("Windows desktop is classified as desktop Windows", () => {
  const input = {
    userAgent:
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      + "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    platform: "Win32",
    maxTouchPoints: 0,
  };

  assert.equal(detectClientPlatform(input), "windows");
  assert.equal(detectClientDeviceClass(input), "desktop");
  assert.equal(detectDesktopPlatform(input), "windows");
  assert.equal(isIOSLikeBrowser(input), false);
});

test("Linux desktop is classified as desktop Linux", () => {
  const input = {
    userAgent:
      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      + "(KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    platform: "Linux x86_64",
    maxTouchPoints: 0,
  };

  assert.equal(detectClientPlatform(input), "linux");
  assert.equal(detectClientDeviceClass(input), "desktop");
  assert.equal(detectDesktopPlatform(input), "linux");
  assert.equal(isIOSLikeBrowser(input), false);
});

test("iPhone is classified as iPhone and never as desktop", () => {
  const input = {
    userAgent:
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
      + "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    platform: "iPhone",
    maxTouchPoints: 5,
  };

  assert.equal(detectClientPlatform(input), "iphone");
  assert.equal(detectClientDeviceClass(input), "phone");
  assert.equal(detectDesktopPlatform(input), null);
  assert.equal(isIOSLikeBrowser(input), true);
});

test("iPod is classified as a phone device class", () => {
  const input = {
    userAgent:
      "Mozilla/5.0 (iPod touch; CPU iPhone OS 16_7 like Mac OS X) AppleWebKit/605.1.15 "
      + "(KHTML, like Gecko) Version/16.7 Mobile/15E148 Safari/604.1",
    platform: "iPod",
    maxTouchPoints: 5,
  };

  assert.equal(detectClientPlatform(input), "iphone");
  assert.equal(detectClientDeviceClass(input), "phone");
});

test("Android Chrome phone is classified as a phone device class", () => {
  const input = {
    userAgent:
      "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
      + "(KHTML, like Gecko) Chrome/123.0.0.0 Mobile Safari/537.36",
    platform: "Linux armv8l",
    maxTouchPoints: 5,
  };

  assert.equal(detectClientPlatform(input), "android");
  assert.equal(detectClientDeviceClass(input), "phone");
});

test("Android tablet user agent is classified as tablet, not phone", () => {
  const input = {
    userAgent:
      "Mozilla/5.0 (Linux; Android 14; Pixel Tablet) AppleWebKit/537.36 "
      + "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    platform: "Linux armv8l",
    maxTouchPoints: 5,
  };

  assert.equal(detectClientPlatform(input), "android");
  assert.equal(detectClientDeviceClass(input), "tablet");
});
