import assert from "node:assert/strict";
import test from "node:test";

import {
  capBrowserPlaybackProfileForDeviceClass,
  detectBrowserPlaybackDeviceClass,
} from "./browserPlaybackDevice.js";

test("iPhone user agent is classified as phone", () => {
  assert.equal(
    detectBrowserPlaybackDeviceClass({
      userAgent:
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
        + "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
      maxTouchPoints: 5,
    }),
    "phone",
  );
});

test("Android Mobile user agent is classified as phone", () => {
  assert.equal(
    detectBrowserPlaybackDeviceClass({
      userAgent:
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
        + "(KHTML, like Gecko) Chrome/123.0.0.0 Mobile Safari/537.36",
      maxTouchPoints: 5,
    }),
    "phone",
  );
});

test("classic iPad user agent is classified as tablet", () => {
  assert.equal(
    detectBrowserPlaybackDeviceClass({
      userAgent:
        "Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
        + "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
      maxTouchPoints: 5,
    }),
    "tablet",
  );
});

test("iPadOS desktop user agent with touch points is classified as tablet", () => {
  assert.equal(
    detectBrowserPlaybackDeviceClass({
      userAgent:
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        + "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
      maxTouchPoints: 5,
    }),
    "tablet",
  );
});

test("desktop platforms are classified as desktop", () => {
  assert.equal(
    detectBrowserPlaybackDeviceClass({
      userAgent:
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        + "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
      maxTouchPoints: 0,
    }),
    "desktop",
  );
  assert.equal(
    detectBrowserPlaybackDeviceClass({
      userAgent:
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        + "(KHTML, like Gecko) Chrome/123.0 Safari/537.36",
      maxTouchPoints: 0,
    }),
    "desktop",
  );
});

test("unknown inputs stay unknown", () => {
  assert.equal(detectBrowserPlaybackDeviceClass({ userAgent: "", maxTouchPoints: 0 }), "unknown");
  assert.equal(
    detectBrowserPlaybackDeviceClass({
      userAgent: "CustomDevice/1.0",
      maxTouchPoints: 0,
    }),
    "unknown",
  );
});

test("phone and unknown cap requested 2160p to 1080p", () => {
  assert.equal(
    capBrowserPlaybackProfileForDeviceClass({
      deviceClass: "phone",
      requestedProfile: "mobile_2160p",
    }),
    "mobile_1080p",
  );
  assert.equal(
    capBrowserPlaybackProfileForDeviceClass({
      deviceClass: "unknown",
      requestedProfile: "mobile_2160p",
    }),
    "mobile_1080p",
  );
});

test("tablet and desktop allow requested 2160p", () => {
  assert.equal(
    capBrowserPlaybackProfileForDeviceClass({
      deviceClass: "tablet",
      requestedProfile: "mobile_2160p",
    }),
    "mobile_2160p",
  );
  assert.equal(
    capBrowserPlaybackProfileForDeviceClass({
      deviceClass: "desktop",
      requestedProfile: "mobile_2160p",
    }),
    "mobile_2160p",
  );
});
