import http from "node:http";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { Readable } from "node:stream";
import { fileURLToPath, pathToFileURL } from "node:url";


const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, "..");
const distDir = path.join(__dirname, "dist");

const frontendHost = process.env.ELVERN_FRONTEND_HOST || "127.0.0.1";
const frontendPort = Number(process.env.ELVERN_FRONTEND_PORT || 4173);
const configuredProxyBodyLimitBytes = Number(process.env.ELVERN_FRONTEND_PROXY_BODY_LIMIT_BYTES || 2 * 1024 * 1024);
const proxyBodyLimitBytes =
  Number.isFinite(configuredProxyBodyLimitBytes) && configuredProxyBodyLimitBytes > 0
    ? configuredProxyBodyLimitBytes
    : 2 * 1024 * 1024;
const configuredBackendHost = process.env.ELVERN_BIND_HOST || "127.0.0.1";
const backendHost =
  configuredBackendHost === "0.0.0.0" || configuredBackendHost === "::" || configuredBackendHost === "[::]"
    ? "127.0.0.1"
    : configuredBackendHost;
const backendProxyOrigin = `http://${backendHost}:${Number(process.env.ELVERN_PORT || 8000)}`;
const distEntry = path.join(distDir, "index.html");
const defaultDbPath = path.join(projectRoot, "backend", "data", "elvern.db");
const dbPath = path.resolve(process.env.ELVERN_DB_PATH || defaultDbPath);
const urlPrefixStatePath = path.join(path.dirname(dbPath), "url_prefix_state.json");
const urlPrefixPattern = /^[a-hjkmnp-z2-9]{8,24}$/;
const manualUrlPrefix = normalizeUrlPrefix(process.env.ELVERN_URL_PREFIX || "");

if (process.env.ELVERN_URL_PREFIX && !manualUrlPrefix) {
  console.error(
    "ELVERN_URL_PREFIX must be 8-24 base32-safe characters using a-h, j-k, m-n, p-z, or 2-9",
  );
  process.exit(1);
}

let urlPrefixCache = {
  prefix: manualUrlPrefix,
  mtimeMs: 0,
};

const mimeTypes = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".ico": "image/x-icon",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".map": "application/json; charset=utf-8",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".txt": "text/plain; charset=utf-8",
  ".webmanifest": "application/manifest+json; charset=utf-8",
};

const hopByHopHeaders = new Set([
  "connection",
  "content-length",
  "host",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);
const spoofableForwardedHeaders = new Set([
  "forwarded",
  "x-forwarded-for",
  "x-real-ip",
]);

const downloadSessionTokenPattern = /\/api\/download\/sessions\/[^/?#\s]+/g;
const neutralRequestTargetOrigin = "http://elvern.local";
const absoluteRequestTargetPattern = /^[a-zA-Z][a-zA-Z0-9+.-]*:/;

export const securityHeaders = {
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "no-referrer",
  "X-Frame-Options": "DENY",
  "Content-Security-Policy": "frame-ancestors 'none'",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
};


class RequestBodyTooLargeError extends Error {
  constructor(limitBytes) {
    super(`Request body exceeds proxy limit of ${limitBytes} bytes`);
    this.limitBytes = limitBytes;
  }
}


export function withSecurityHeaders(headers = {}) {
  const merged = { ...headers };
  const existingNames = new Set(Object.keys(merged).map((key) => key.toLowerCase()));
  for (const [name, value] of Object.entries(securityHeaders)) {
    const normalizedName = name.toLowerCase();
    if (!existingNames.has(normalizedName)) {
      merged[name] = value;
      existingNames.add(normalizedName);
    }
  }
  return merged;
}


export function applySecurityHeadersToResponse(response) {
  for (const [name, value] of Object.entries(securityHeaders)) {
    const hasHeader =
      typeof response.hasHeader === "function"
        ? response.hasHeader(name)
        : Object.keys(response.getHeaders?.() || {}).some((key) => key.toLowerCase() === name.toLowerCase());
    if (!hasHeader) {
      response.setHeader(name, value);
    }
  }
}


export function buildAssetResponseHeaders(contentType, cacheControl, { appShell = false } = {}) {
  const headers = {
    "Content-Type": contentType,
    "Cache-Control": cacheControl,
  };
  if (appShell) {
    headers["X-Elvern-App-Shell"] = "1";
  }
  return withSecurityHeaders(headers);
}


export function resolveAssetCacheControl(filePath) {
  const normalizedPath = path.normalize(filePath);
  const extension = path.extname(normalizedPath);
  const basename = path.basename(normalizedPath);
  const isFavicon = basename.startsWith("favicon");
  return basename === "sw.js" || extension === ".webmanifest" || extension === ".html" || isFavicon
    ? "no-cache"
    : "public, max-age=31536000, immutable";
}


export function sendError(response, statusCode, message) {
  response.writeHead(statusCode, withSecurityHeaders({ "Content-Type": "text/plain; charset=utf-8" }));
  response.end(message);
}

export function sendMethodNotAllowed(response, allowedMethods) {
  response.writeHead(
    405,
    withSecurityHeaders({
      "Allow": allowedMethods.join(", "),
      "Content-Type": "text/plain; charset=utf-8",
    }),
  );
  response.end("Method Not Allowed");
}

export function sendFrontendHealth(response) {
  response.writeHead(204, withSecurityHeaders({
    "Cache-Control": "no-store",
  }));
  response.end();
}

function redactSensitiveUrl(value) {
  return String(value || "").replace(downloadSessionTokenPattern, "/api/download/sessions/[redacted]");
}

function normalizeUrlPrefix(value) {
  const prefix = String(value || "").trim().replace(/^\/+|\/+$/g, "").toLowerCase();
  return urlPrefixPattern.test(prefix) ? prefix : null;
}

async function getActiveUrlPrefix() {
  if (manualUrlPrefix) {
    return manualUrlPrefix;
  }

  let stat;
  try {
    stat = await fsp.stat(urlPrefixStatePath);
  } catch {
    urlPrefixCache = { prefix: null, mtimeMs: 0 };
    return null;
  }

  if (urlPrefixCache.prefix && urlPrefixCache.mtimeMs === stat.mtimeMs) {
    return urlPrefixCache.prefix;
  }

  try {
    const payload = JSON.parse(await fsp.readFile(urlPrefixStatePath, "utf-8"));
    const prefix = normalizeUrlPrefix(payload.prefix);
    urlPrefixCache = { prefix, mtimeMs: stat.mtimeMs };
    return prefix;
  } catch {
    urlPrefixCache = { prefix: null, mtimeMs: stat.mtimeMs };
    return null;
  }
}

function pathIsInsideDist(candidatePath) {
  const relativePath = path.relative(distDir, candidatePath);
  return relativePath === "" || (!relativePath.startsWith("..") && !path.isAbsolute(relativePath));
}

function withBaseHref(html, urlPrefix) {
  const baseTag = `<base href="/${urlPrefix}/">`;
  if (/<base\s/i.test(html)) {
    return html.replace(/<base\s[^>]*>/i, baseTag);
  }
  return html.replace(/<head([^>]*)>/i, `<head$1>\n    ${baseTag}`);
}


function firstHeaderValue(value) {
  if (Array.isArray(value)) {
    return value.find((item) => typeof item === "string" && item.trim()) || "";
  }
  return typeof value === "string" ? value : "";
}


function requestRemoteAddress(request) {
  return request.socket?.remoteAddress || request.connection?.remoteAddress || "";
}


function requestForwardedProto(request) {
  return request.socket?.encrypted || request.connection?.encrypted ? "https" : "http";
}


async function readBody(request) {
  if (request.method === "GET" || request.method === "HEAD") {
    return undefined;
  }
  const declaredLength = Number(request.headers["content-length"] || 0);
  if (Number.isFinite(declaredLength) && declaredLength > proxyBodyLimitBytes) {
    throw new RequestBodyTooLargeError(proxyBodyLimitBytes);
  }
  let totalBytes = 0;
  const chunks = [];
  for await (const chunk of request) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    totalBytes += buffer.length;
    if (totalBytes > proxyBodyLimitBytes) {
      throw new RequestBodyTooLargeError(proxyBodyLimitBytes);
    }
    chunks.push(buffer);
  }
  return chunks.length > 0 ? Buffer.concat(chunks) : undefined;
}


export function isAbsoluteRequestTarget(rawUrl) {
  const candidate = String(rawUrl || "");
  return absoluteRequestTargetPattern.test(candidate) || candidate.startsWith("//");
}


export function resolveSafeRequestTarget(rawUrl) {
  const candidate = String(rawUrl || "");
  if (!candidate || !candidate.startsWith("/") || isAbsoluteRequestTarget(candidate)) {
    return null;
  }

  let parsedUrl;
  try {
    parsedUrl = new URL(candidate, neutralRequestTargetOrigin);
  } catch {
    return null;
  }

  if (parsedUrl.origin !== neutralRequestTargetOrigin || !parsedUrl.pathname.startsWith("/")) {
    return null;
  }

  let decodedPathname;
  try {
    decodedPathname = decodeURIComponent(parsedUrl.pathname);
  } catch {
    return null;
  }

  if (!decodedPathname.startsWith("/")) {
    return null;
  }

  return {
    pathname: parsedUrl.pathname,
    decodedPathname,
    search: parsedUrl.search || "",
  };
}


export function buildBackendProxyUrl(parsedTarget, backendOrigin = backendProxyOrigin) {
  if (!parsedTarget || !parsedTarget.pathname || !parsedTarget.pathname.startsWith("/")) {
    return null;
  }

  let expectedBackendOrigin;
  let targetUrl;
  try {
    expectedBackendOrigin = new URL(backendOrigin).origin;
    targetUrl = new URL(`${parsedTarget.pathname}${parsedTarget.search || ""}`, expectedBackendOrigin);
  } catch {
    return null;
  }

  if (targetUrl.origin !== expectedBackendOrigin) {
    return null;
  }

  return targetUrl;
}


export function classifyFrontendRequestTarget(parsedTarget, urlPrefix, method = "GET") {
  if (!parsedTarget) {
    return { kind: "invalid" };
  }

  if (parsedTarget.pathname.startsWith("/api/")) {
    return { kind: "proxy", route: "api" };
  }

  if (parsedTarget.pathname === "/_elvern/frontend-health") {
    if (method === "GET" || method === "HEAD") {
      return { kind: "frontend_health", route: "frontend_health" };
    }
    return { kind: "method_not_allowed", route: "frontend_health", allowedMethods: ["GET", "HEAD"] };
  }

  if (parsedTarget.pathname === "/health") {
    return { kind: "proxy", route: "health" };
  }

  if (urlPrefix && parsedTarget.decodedPathname === `/${urlPrefix}/manifest.webmanifest`) {
    if (method === "GET" || method === "HEAD") {
      return { kind: "proxy", route: "manifest" };
    }
    return { kind: "method_not_allowed", route: "manifest", allowedMethods: ["GET", "HEAD"] };
  }

  return { kind: "asset" };
}


async function proxyRequest(request, response, targetUrl) {
  const expectedBackendOrigin = new URL(backendProxyOrigin).origin;
  if (!targetUrl || targetUrl.origin !== expectedBackendOrigin) {
    sendError(response, 502, "Invalid upstream target");
    return;
  }

  const requestHeaders = new Headers();

  for (const [name, value] of Object.entries(request.headers)) {
    const normalizedName = name.toLowerCase();
    if (!value || hopByHopHeaders.has(normalizedName) || spoofableForwardedHeaders.has(normalizedName)) {
      continue;
    }
    if (Array.isArray(value)) {
      for (const item of value) {
        requestHeaders.append(name, item);
      }
      continue;
    }
    requestHeaders.set(name, value);
  }
  const remoteAddress = requestRemoteAddress(request).trim();
  if (remoteAddress) {
    requestHeaders.set("X-Forwarded-For", remoteAddress);
  }
  const forwardedHost = firstHeaderValue(request.headers.host).trim();
  if (forwardedHost) {
    requestHeaders.set("X-Forwarded-Host", forwardedHost);
  }
  requestHeaders.set("X-Forwarded-Proto", requestForwardedProto(request));

  const body = await readBody(request);
  const upstream = await fetch(targetUrl, {
    method: request.method,
    headers: requestHeaders,
    body,
    redirect: "manual",
  });

  response.statusCode = upstream.status;
  upstream.headers.forEach((value, key) => {
    if (hopByHopHeaders.has(key.toLowerCase())) {
      return;
    }
    response.setHeader(key, value);
  });
  applySecurityHeadersToResponse(response);

  if (request.method === "HEAD") {
    response.end();
    return;
  }

  if (upstream.body) {
    response.flushHeaders();
    Readable.fromWeb(upstream.body).pipe(response);
    return;
  }

  response.end();
}


export async function handleFrontendRequest(request, response) {
  try {
    if (!request.url) {
      sendError(response, 400, "Bad Request");
      return;
    }

    const parsedTarget = resolveSafeRequestTarget(request.url);
    if (!parsedTarget) {
      sendError(response, 400, "Bad Request");
      return;
    }

    const initialRoute = classifyFrontendRequestTarget(parsedTarget, null, request.method);
    if (initialRoute.kind === "frontend_health") {
      sendFrontendHealth(response);
      return;
    }
    if (initialRoute.kind === "proxy") {
      await proxyRequest(request, response, buildBackendProxyUrl(parsedTarget));
      return;
    }

    const urlPrefix = await getActiveUrlPrefix();
    const route = classifyFrontendRequestTarget(parsedTarget, urlPrefix, request.method);
    if (route.kind === "proxy") {
      await proxyRequest(request, response, buildBackendProxyUrl(parsedTarget));
      return;
    }
    if (route.kind === "method_not_allowed") {
      sendMethodNotAllowed(response, route.allowedMethods);
      return;
    }

    await serveAsset(request, response);
  } catch (error) {
    if (error instanceof RequestBodyTooLargeError) {
      sendError(response, 413, "Request body too large");
      return;
    }
    console.error("Elvern frontend server error", {
      url: redactSensitiveUrl(request.url),
      error,
    });
    sendError(response, 502, "Upstream request failed");
  }
}


export function createFrontendServer() {
  return http.createServer(handleFrontendRequest);
}


async function resolveAsset(requestUrl) {
  const parsedUrl = new URL(requestUrl, "http://elvern.local");
  const requestPath = decodeURIComponent(parsedUrl.pathname);
  const urlPrefix = await getActiveUrlPrefix();

  if (!urlPrefix) {
    return null;
  }

  const prefixBase = `/${urlPrefix}`;
  if (requestPath !== prefixBase && !requestPath.startsWith(`${prefixBase}/`)) {
    return null;
  }

  const pathAfterPrefix = requestPath.slice(prefixBase.length) || "/";
  const normalizedPath = pathAfterPrefix === "/" ? "/index.html" : pathAfterPrefix;
  const candidatePath = path.normalize(path.join(distDir, normalizedPath));

  if (!pathIsInsideDist(candidatePath)) {
    return null;
  }

  try {
    const stat = await fsp.stat(candidatePath);
    if (stat.isFile()) {
      return { filePath: candidatePath, urlPrefix };
    }
    if (stat.isDirectory()) {
      return { filePath: path.join(candidatePath, "index.html"), urlPrefix };
    }
  } catch {
    if (path.extname(normalizedPath)) {
      return null;
    }
    return { filePath: path.join(distDir, "index.html"), urlPrefix };
  }

  return { filePath: path.join(distDir, "index.html"), urlPrefix };
}


async function serveAsset(request, response) {
  const resolved = await resolveAsset(request.url);
  if (!resolved) {
    sendError(response, 404, "Not Found");
    return;
  }
  const { filePath, urlPrefix } = resolved;

  try {
    await fsp.access(filePath);
  } catch {
    sendError(response, 404, "Not Found");
    return;
  }

  const extension = path.extname(filePath);
  const normalizedPath = path.normalize(filePath);
  const contentType = mimeTypes[extension] || "application/octet-stream";
  const isIndexHtml = normalizedPath === path.normalize(distEntry);
  const cacheControl = resolveAssetCacheControl(normalizedPath);

  if (isIndexHtml) {
    const html = withBaseHref(await fsp.readFile(filePath, "utf-8"), urlPrefix);
    response.writeHead(200, buildAssetResponseHeaders(contentType, cacheControl, { appShell: true }));
    if (request.method === "HEAD") {
      response.end();
      return;
    }
    response.end(html);
    return;
  }

  response.writeHead(200, buildAssetResponseHeaders(contentType, cacheControl));

  fs.createReadStream(filePath).pipe(response);
}


function isMainModule() {
  return process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href;
}


if (isMainModule()) {
  try {
    await fsp.access(distEntry);
  } catch {
    console.error("Missing frontend/dist/index.html. Run 'npm run build' in frontend/ first.");
    process.exit(1);
  }

  const server = createFrontendServer();
  server.listen(frontendPort, frontendHost, () => {
    console.log(
      `Elvern frontend listening on http://${frontendHost}:${frontendPort} and proxying to ${backendProxyOrigin}`,
    );
  });
}
