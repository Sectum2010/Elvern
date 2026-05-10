import http from "node:http";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { Readable } from "node:stream";
import { fileURLToPath } from "node:url";


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

const downloadSessionTokenPattern = /\/api\/download\/sessions\/[^/?#\s]+/g;


class RequestBodyTooLargeError extends Error {
  constructor(limitBytes) {
    super(`Request body exceeds proxy limit of ${limitBytes} bytes`);
    this.limitBytes = limitBytes;
  }
}


function sendError(response, statusCode, message) {
  response.writeHead(statusCode, { "Content-Type": "text/plain; charset=utf-8" });
  response.end(message);
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


async function proxyRequest(request, response) {
  const targetUrl = new URL(request.url, backendProxyOrigin);
  const requestHeaders = new Headers();

  for (const [name, value] of Object.entries(request.headers)) {
    if (!value || hopByHopHeaders.has(name.toLowerCase())) {
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
  const basename = path.basename(normalizedPath);
  const isFavicon = basename.startsWith("favicon");
  const isIndexHtml = normalizedPath === path.normalize(distEntry);
  const cacheControl =
    normalizedPath === path.join(distDir, "sw.js") || extension === ".webmanifest"
      ? "no-cache"
      : extension === ".html" || isFavicon
      ? "no-cache"
      : "public, max-age=31536000, immutable";

  if (isIndexHtml) {
    const html = withBaseHref(await fsp.readFile(filePath, "utf-8"), urlPrefix);
    response.writeHead(200, {
      "Content-Type": contentType,
      "Cache-Control": cacheControl,
    });
    if (request.method === "HEAD") {
      response.end();
      return;
    }
    response.end(html);
    return;
  }

  response.writeHead(200, {
    "Content-Type": contentType,
    "Cache-Control": cacheControl,
  });

  fs.createReadStream(filePath).pipe(response);
}


const server = http.createServer(async (request, response) => {
  try {
    if (!request.url) {
      sendError(response, 400, "Bad Request");
      return;
    }

    if (request.url.startsWith("/api/") || request.url === "/health") {
      await proxyRequest(request, response);
      return;
    }

    // The manifest must come from the backend so URL-prefix paths are rewritten.
    // The static dist/manifest.webmanifest is only the unprefixed template.
    const urlPrefix = await getActiveUrlPrefix();
    if (urlPrefix) {
      const manifestPath = `/${urlPrefix}/manifest.webmanifest`;
      const parsedUrl = new URL(request.url, "http://elvern.local");
      if (decodeURIComponent(parsedUrl.pathname) === manifestPath) {
        await proxyRequest(request, response);
        return;
      }
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
});

try {
  await fsp.access(distEntry);
} catch {
  console.error("Missing frontend/dist/index.html. Run 'npm run build' in frontend/ first.");
  process.exit(1);
}


server.listen(frontendPort, frontendHost, () => {
  console.log(
    `Elvern frontend listening on http://${frontendHost}:${frontendPort} and proxying to ${backendProxyOrigin}`,
  );
});
