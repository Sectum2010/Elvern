import { describe, expect, test } from "vitest";

import {
  applySecurityHeadersToResponse,
  buildAssetResponseHeaders,
  buildBackendProxyUrl,
  classifyFrontendRequestTarget,
  handleFrontendRequest,
  isAbsoluteRequestTarget,
  resolveSafeRequestTarget,
  securityHeaders,
  sendMethodNotAllowed,
  withSecurityHeaders,
} from "../../server.mjs";

const backendOrigin = "http://127.0.0.1:8000";
const activePrefix = "abcd2345";
const expectedSecurityHeaders = {
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
  "X-Frame-Options": "DENY",
  "Content-Security-Policy": "frame-ancestors 'none'",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
};

function createMockResponse() {
  return {
    body: "",
    ended: false,
    headers: new Map(),
    statusCode: 200,
    writeHead(statusCode, headers = {}) {
      this.statusCode = statusCode;
      for (const [name, value] of Object.entries(headers)) {
        this.setHeader(name, value);
      }
    },
    setHeader(name, value) {
      this.headers.set(name.toLowerCase(), { name, value });
    },
    hasHeader(name) {
      return this.headers.has(name.toLowerCase());
    },
    getHeader(name) {
      return this.headers.get(name.toLowerCase())?.value;
    },
    getHeaders() {
      return Object.fromEntries(Array.from(this.headers.values()).map(({ name, value }) => [name, value]));
    },
    end(body = "") {
      this.ended = true;
      this.body = body;
    },
  };
}

function expectGlobalSecurityHeaders(response) {
  for (const [name, value] of Object.entries(expectedSecurityHeaders)) {
    expect(response.getHeader(name)).toBe(value);
  }
  expect(response.hasHeader("Strict-Transport-Security")).toBe(false);
}

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

  test("approved global security headers are low-regression only", () => {
    expect(securityHeaders).toEqual(expectedSecurityHeaders);
    expect(securityHeaders).not.toHaveProperty("Strict-Transport-Security");
    expect(securityHeaders["Content-Security-Policy"]).toBe("frame-ancestors 'none'");
    expect(securityHeaders["Content-Security-Policy"]).not.toContain("default-src");
    expect(securityHeaders["Content-Security-Policy"]).not.toContain("script-src");
    expect(securityHeaders["Content-Security-Policy"]).not.toContain("style-src");
    expect(securityHeaders["Content-Security-Policy"]).not.toContain("media-src");
    expect(securityHeaders["Content-Security-Policy"]).not.toContain("connect-src");
  });

  test("static index/html response headers include global security headers", () => {
    const headers = buildAssetResponseHeaders("text/html; charset=utf-8", "no-cache");

    expect(headers["Content-Type"]).toBe("text/html; charset=utf-8");
    expect(headers["Cache-Control"]).toBe("no-cache");
    expect(headers).toMatchObject(expectedSecurityHeaders);
    expect(headers).not.toHaveProperty("Strict-Transport-Security");
  });

  test("static JS/CSS asset response headers include global security headers", () => {
    const jsHeaders = buildAssetResponseHeaders("text/javascript; charset=utf-8", "public, max-age=31536000, immutable");
    const cssHeaders = buildAssetResponseHeaders("text/css; charset=utf-8", "public, max-age=31536000, immutable");

    expect(jsHeaders).toMatchObject(expectedSecurityHeaders);
    expect(cssHeaders).toMatchObject(expectedSecurityHeaders);
    expect(jsHeaders).not.toHaveProperty("Strict-Transport-Security");
    expect(cssHeaders).not.toHaveProperty("Strict-Transport-Security");
  });

  test("404 sendError response includes global security headers", async () => {
    const response = createMockResponse();

    await handleFrontendRequest({ headers: {}, method: "GET", url: "/missing" }, response);

    expect(response.statusCode).toBe(404);
    expect(response.body).toBe("Not Found");
    expectGlobalSecurityHeaders(response);
  });

  test("405 manifest method-not-allowed response includes global security headers", () => {
    const response = createMockResponse();

    sendMethodNotAllowed(response, ["GET", "HEAD"]);

    expect(response.statusCode).toBe(405);
    expect(response.getHeader("Allow")).toBe("GET, HEAD");
    expect(response.body).toBe("Method Not Allowed");
    expectGlobalSecurityHeaders(response);
  });

  test("proxied backend response gets missing global security headers added", async () => {
    const originalFetch = globalThis.fetch;
    const response = createMockResponse();
    globalThis.fetch = async () => new Response(null, {
      headers: { "Content-Type": "application/json; charset=utf-8" },
      status: 204,
    });

    try {
      await handleFrontendRequest({ headers: {}, method: "GET", url: "/api/library" }, response);
    } finally {
      globalThis.fetch = originalFetch;
    }

    expect(response.statusCode).toBe(204);
    expect(response.getHeader("Content-Type")).toBe("application/json; charset=utf-8");
    expectGlobalSecurityHeaders(response);
  });

  test("proxied backend response preserves upstream route-specific CSP", async () => {
    const originalFetch = globalThis.fetch;
    const response = createMockResponse();
    globalThis.fetch = async () => new Response(null, {
      headers: {
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Content-Type": "application/octet-stream",
      },
      status: 200,
    });

    try {
      await handleFrontendRequest({ headers: {}, method: "GET", url: "/api/assistant/attachments/1" }, response);
    } finally {
      globalThis.fetch = originalFetch;
    }

    expect(response.statusCode).toBe(200);
    expect(response.getHeader("Content-Security-Policy")).toBe("default-src 'none'; sandbox");
    expect(response.getHeader("X-Frame-Options")).toBe("DENY");
    expect(response.hasHeader("Strict-Transport-Security")).toBe(false);
  });

  test("withSecurityHeaders preserves caller supplied CSP case-insensitively", () => {
    const headers = withSecurityHeaders({
      "content-security-policy": "default-src 'none'; sandbox",
      "Content-Type": "application/octet-stream",
    });

    expect(headers["content-security-policy"]).toBe("default-src 'none'; sandbox");
    expect(headers["Content-Security-Policy"]).toBeUndefined();
    expect(headers["Content-Type"]).toBe("application/octet-stream");
    expect(headers).toMatchObject({
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "no-referrer",
      "X-Frame-Options": "DENY",
      "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    });
  });

  test("applySecurityHeadersToResponse does not overwrite existing response CSP", () => {
    const response = createMockResponse();
    response.setHeader("Content-Security-Policy", "default-src 'none'; sandbox");

    applySecurityHeadersToResponse(response);

    expect(response.getHeader("Content-Security-Policy")).toBe("default-src 'none'; sandbox");
    expect(response.getHeader("X-Content-Type-Options")).toBe("nosniff");
    expect(response.hasHeader("Strict-Transport-Security")).toBe(false);
  });
});
