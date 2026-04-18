import http from "node:http";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import { Readable } from "node:stream";
import { fileURLToPath } from "node:url";


const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const distDir = path.join(__dirname, "dist");

const frontendHost = process.env.ELVERN_FRONTEND_HOST || "127.0.0.1";
const frontendPort = Number(process.env.ELVERN_FRONTEND_PORT || 4173);
const configuredBackendHost = process.env.ELVERN_BIND_HOST || "127.0.0.1";
const backendHost =
  configuredBackendHost === "0.0.0.0" || configuredBackendHost === "::" || configuredBackendHost === "[::]"
    ? "127.0.0.1"
    : configuredBackendHost;
const backendProxyOrigin = `http://${backendHost}:${Number(process.env.ELVERN_PORT || 8000)}`;
const distEntry = path.join(distDir, "index.html");

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


function sendError(response, statusCode, message) {
  response.writeHead(statusCode, { "Content-Type": "text/plain; charset=utf-8" });
  response.end(message);
}


async function readBody(request) {
  if (request.method === "GET" || request.method === "HEAD") {
    return undefined;
  }
  const chunks = [];
  for await (const chunk of request) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
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
  const normalizedPath = requestPath === "/" ? "/index.html" : requestPath;
  const candidatePath = path.normalize(path.join(distDir, normalizedPath));

  if (!candidatePath.startsWith(distDir)) {
    return null;
  }

  try {
    const stat = await fsp.stat(candidatePath);
    if (stat.isFile()) {
      return candidatePath;
    }
    if (stat.isDirectory()) {
      return path.join(candidatePath, "index.html");
    }
  } catch {
    if (path.extname(normalizedPath)) {
      return null;
    }
    return path.join(distDir, "index.html");
  }

  return path.join(distDir, "index.html");
}


async function serveAsset(request, response) {
  const filePath = await resolveAsset(request.url);
  if (!filePath) {
    sendError(response, 404, "Not Found");
    return;
  }

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
  const cacheControl =
    normalizedPath === path.join(distDir, "sw.js") || extension === ".webmanifest"
      ? "no-cache"
      : extension === ".html" || isFavicon
      ? "no-cache"
      : "public, max-age=31536000, immutable";

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

    await serveAsset(request, response);
  } catch (error) {
    console.error("Elvern frontend server error", error);
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
