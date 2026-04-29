import assert from "node:assert/strict";
import test from "node:test";

import { resolveBrowserHlsEngine } from "./browserHlsEngine.js";

test("Windows desktop prefers hls.js over native HLS maybe support", () => {
  assert.equal(resolveBrowserHlsEngine({
    deviceClass: "desktop",
    hlsJsSupported: true,
    iosMobile: false,
    nativeHlsSupport: "maybe",
  }), "hls.js");
});

test("Linux desktop uses hls.js when native HLS is unavailable", () => {
  assert.equal(resolveBrowserHlsEngine({
    deviceClass: "desktop",
    hlsJsSupported: true,
    iosMobile: false,
    nativeHlsSupport: "",
  }), "hls.js");
});

test("macOS desktop Chromium-style browsers prefer hls.js when available", () => {
  assert.equal(resolveBrowserHlsEngine({
    deviceClass: "desktop",
    hlsJsSupported: true,
    iosMobile: false,
    nativeHlsSupport: "probably",
  }), "hls.js");
});

test("macOS Safari can still fall back to native HLS when hls.js is unsupported", () => {
  assert.equal(resolveBrowserHlsEngine({
    deviceClass: "desktop",
    hlsJsSupported: false,
    iosMobile: false,
    nativeHlsSupport: "probably",
  }), "native_hls");
});

test("iPhone and iPad native HLS behavior remains unchanged", () => {
  for (const deviceClass of ["phone", "tablet"]) {
    assert.equal(resolveBrowserHlsEngine({
      deviceClass,
      hlsJsSupported: true,
      iosMobile: true,
      nativeHlsSupport: "probably",
    }), "native_hls");
  }
});

test("non-iOS mobile clients keep existing native HLS priority when advertised", () => {
  assert.equal(resolveBrowserHlsEngine({
    deviceClass: "phone",
    hlsJsSupported: true,
    iosMobile: false,
    nativeHlsSupport: "maybe",
  }), "native_hls");
});

test("unsupported HLS is reported only when neither engine is available", () => {
  assert.equal(resolveBrowserHlsEngine({
    deviceClass: "desktop",
    hlsJsSupported: false,
    iosMobile: false,
    nativeHlsSupport: "",
  }), "unsupported_hls");
});
