import { describe, expect, test } from "vitest";

import {
  buildBackendProxyUrl,
  classifyFrontendRequestTarget,
  isAbsoluteRequestTarget,
  resolveSafeRequestTarget,
} from "../../server.mjs";

const backendOrigin = "http://127.0.0.1:8000";
const activePrefix = "abcd2345";

function trustedBackendUrl(rawUrl) {
  const parsedTarget = resolveSafeRequestTarget(rawUrl);
  return buildBackendProxyUrl(parsedTarget, backendOrigin);
}

describe("frontend production server proxy target hardening", () => {
  test("api requests resolve to the configured backend origin and preserve path and query", () => {
    const parsedTarget = resolveSafeRequestTarget("/api/library?category=movies");
    const targetUrl = buildBackendProxyUrl(parsedTarget, backendOrigin);

    expect(classifyFrontendRequestTarget(parsedTarget, activePrefix, "GET")).toEqual({
      kind: "proxy",
      route: "api",
    });
    expect(targetUrl.origin).toBe(backendOrigin);
    expect(targetUrl.pathname).toBe("/api/library");
    expect(targetUrl.search).toBe("?category=movies");
  });

  test("health requests resolve only to the configured backend origin", () => {
    const parsedTarget = resolveSafeRequestTarget("/health");
    const targetUrl = buildBackendProxyUrl(parsedTarget, backendOrigin);

    expect(classifyFrontendRequestTarget(parsedTarget, activePrefix, "GET")).toEqual({
      kind: "proxy",
      route: "health",
    });
    expect(targetUrl.origin).toBe(backendOrigin);
    expect(targetUrl.pathname).toBe("/health");
  });

  test("active-prefix manifest requests resolve to the configured backend origin", () => {
    const parsedTarget = resolveSafeRequestTarget(`/${activePrefix}/manifest.webmanifest?version=1`);
    const targetUrl = buildBackendProxyUrl(parsedTarget, backendOrigin);

    expect(classifyFrontendRequestTarget(parsedTarget, activePrefix, "HEAD")).toEqual({
      kind: "proxy",
      route: "manifest",
    });
    expect(targetUrl.origin).toBe(backendOrigin);
    expect(targetUrl.pathname).toBe(`/${activePrefix}/manifest.webmanifest`);
    expect(targetUrl.search).toBe("?version=1");
  });

  test("absolute-form and protocol-relative request targets are rejected before proxying", () => {
    expect(isAbsoluteRequestTarget(`http://evil.example/${activePrefix}/manifest.webmanifest`)).toBe(true);
    expect(resolveSafeRequestTarget(`http://evil.example/${activePrefix}/manifest.webmanifest`)).toBeNull();
    expect(resolveSafeRequestTarget("//evil.example/abcd2345/manifest.webmanifest")).toBeNull();
    expect(resolveSafeRequestTarget("http://evil.example/api/library")).toBeNull();
  });

  test("manifest POST is not proxy-eligible", () => {
    const parsedTarget = resolveSafeRequestTarget(`/${activePrefix}/manifest.webmanifest`);

    expect(classifyFrontendRequestTarget(parsedTarget, activePrefix, "POST")).toEqual({
      kind: "method_not_allowed",
      route: "manifest",
      allowedMethods: ["GET", "HEAD"],
    });
  });

  test("static assets remain outside the proxy route allowlist", () => {
    const parsedTarget = resolveSafeRequestTarget(`/${activePrefix}/assets/index.js`);

    expect(classifyFrontendRequestTarget(parsedTarget, activePrefix, "GET")).toEqual({
      kind: "asset",
    });
  });

  test("trusted backend URL builder fixes origin even when query parameters contain external URLs", () => {
    const targetUrl = trustedBackendUrl("/api/library?next=http%3A%2F%2Fevil.example%2Fcallback");

    expect(targetUrl.origin).toBe(backendOrigin);
    expect(targetUrl.href).toBe(
      `${backendOrigin}/api/library?next=http%3A%2F%2Fevil.example%2Fcallback`,
    );
  });

  test("malformed request targets are rejected", () => {
    expect(resolveSafeRequestTarget("api/library")).toBeNull();
    expect(resolveSafeRequestTarget("")).toBeNull();
    expect(resolveSafeRequestTarget("/api/%E0%A4%A")).toBeNull();
  });
});
