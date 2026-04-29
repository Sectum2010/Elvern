import assert from "node:assert/strict";
import test from "node:test";

import {
  resolveDetailVlcActionRoute,
  shouldShowDesktopBrowserSeekControl,
  shouldShowMacAppFullscreenControl,
  shouldShowMacHlsWindowControls,
} from "./playbackRouting.js";

test("macOS desktop VLC uses desktop helper handoff when direct open is not available", () => {
  const route = resolveDetailVlcActionRoute({
    desktopPlatform: "mac",
    iosMobile: false,
    desktopPlayback: {
      handoff_supported: true,
      open_method: "protocol_helper",
      open_supported: false,
      same_host_launch: false,
    },
  });

  assert.equal(route.surface, "desktop_helper");
  assert.equal(route.endpoint, "desktop_handoff");
});

test("Windows desktop VLC uses desktop helper handoff when direct open is not available", () => {
  const route = resolveDetailVlcActionRoute({
    desktopPlatform: "windows",
    iosMobile: false,
    desktopPlayback: {
      handoff_supported: true,
      open_method: "protocol_helper",
      open_supported: false,
      same_host_launch: false,
    },
  });

  assert.equal(route.surface, "desktop_helper");
  assert.equal(route.endpoint, "desktop_handoff");
});

test("Linux same-host VLC keeps the direct desktop open path", () => {
  const route = resolveDetailVlcActionRoute({
    desktopPlatform: "linux",
    iosMobile: false,
    desktopPlayback: {
      handoff_supported: false,
      open_method: "spawn_vlc",
      open_supported: true,
      same_host_launch: true,
    },
  });

  assert.equal(route.surface, "desktop_open");
  assert.equal(route.endpoint, "desktop_open");
});

test("iPad and iPhone route VLC through iOS external app handoff, not desktop helper", () => {
  for (const desktopPlatform of [null, "mac"]) {
    const route = resolveDetailVlcActionRoute({
      desktopPlatform,
      iosMobile: true,
      desktopPlayback: {
        handoff_supported: true,
        open_method: "protocol_helper",
      },
    });

    assert.equal(route.surface, "ios_external");
    assert.equal(route.endpoint, "ios_external_app");
  }
});

test("Mac-only HLS and absolute seek controls do not appear on iPad or iPhone", () => {
  const common = {
    showPlayerShell: true,
    hasMobileSession: true,
    fullDuration: 7200,
    playerLocalDuration: 420,
  };

  assert.equal(shouldShowDesktopBrowserSeekControl({
    ...common,
    desktopPlatform: null,
    iosMobile: true,
  }), false);
  assert.equal(shouldShowMacHlsWindowControls({
    ...common,
    desktopPlatform: null,
    iosMobile: true,
  }), false);
  assert.equal(shouldShowMacAppFullscreenControl({
    desktopPlatform: null,
    iosMobile: true,
    showPlayerShell: true,
  }), false);
});

test("real Mac desktop can show Mac-only window controls and full movie seek", () => {
  assert.equal(shouldShowDesktopBrowserSeekControl({
    desktopPlatform: "mac",
    iosMobile: false,
    showPlayerShell: true,
    hasMobileSession: true,
    fullDuration: 7200,
  }), true);
  assert.equal(shouldShowMacHlsWindowControls({
    desktopPlatform: "mac",
    iosMobile: false,
    showPlayerShell: true,
    hasMobileSession: true,
    playerLocalDuration: 420,
  }), true);
  assert.equal(shouldShowMacAppFullscreenControl({
    desktopPlatform: "mac",
    iosMobile: false,
    showPlayerShell: true,
  }), true);
});

test("non-desktop mobile clients never get desktop absolute seek controls", () => {
  assert.equal(shouldShowDesktopBrowserSeekControl({
    desktopPlatform: null,
    iosMobile: false,
    showPlayerShell: true,
    hasMobileSession: true,
    fullDuration: 7200,
  }), false);
});

test("full movie absolute seek remains Mac-only among desktop platforms", () => {
  for (const desktopPlatform of ["windows", "linux"]) {
    assert.equal(shouldShowDesktopBrowserSeekControl({
      desktopPlatform,
      iosMobile: false,
      showPlayerShell: true,
      hasMobileSession: true,
      fullDuration: 7200,
    }), false);
  }
});
