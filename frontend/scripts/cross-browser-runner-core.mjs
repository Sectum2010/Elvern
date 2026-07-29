import { randomBytes } from "node:crypto";
import { createHash } from "node:crypto";
import {
  closeSync,
  lstatSync,
  mkdirSync,
  openSync,
  readSync,
  readdirSync,
  readFileSync,
  statSync,
  unlinkSync,
} from "node:fs";
import http from "node:http";
import https from "node:https";
import net from "node:net";
import { tmpdir } from "node:os";
import { join, relative, resolve, sep } from "node:path";


const BASE32_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789";
const reservedPorts = new Set();
const reservedPortLocks = new Map();
const lockDirectory = join(tmpdir(), "elvern-playwright-ports");
export const PHASE7_BUILD_CONTRACT = Object.freeze({
  schema_version: "elvern-frontend-build-contract-v2",
  library_summary_v2_mode: "on",
  library_revision_mode: "on",
  build_kind: "phase7-production",
});
export const PHASE7_BUILD_CONTRACT_FILENAME = ".elvern-build-contract.json";
const NETWORK_GUARD_CONTROL_HEADER = "x-elvern-network-guard-token";


export function createCrossBrowserPrefix(byteCount = 12) {
  return Array.from(
    randomBytes(byteCount),
    (value) => BASE32_ALPHABET[value % BASE32_ALPHABET.length],
  ).join("");
}


export async function reserveAvailablePort(host = "127.0.0.1") {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once("error", reject);
    server.listen(0, host, () => {
      const address = server.address();
      const port = typeof address === "object" && address ? Number(address.port) : 0;
      server.close((error) => {
        if (error) {
          reject(error);
          return;
        }
        if (!port || reservedPorts.has(port)) {
          reserveAvailablePort(host).then(resolve, reject);
          return;
        }
        mkdirSync(lockDirectory, { recursive: true });
        const lockPath = join(lockDirectory, `${port}.lock`);
        let lockDescriptor;
        try {
          lockDescriptor = openSync(lockPath, "wx");
        } catch (lockError) {
          if (lockError?.code === "EEXIST") {
            reserveAvailablePort(host).then(resolve, reject);
            return;
          }
          reject(lockError);
          return;
        }
        reservedPorts.add(port);
        reservedPortLocks.set(port, { descriptor: lockDescriptor, path: lockPath });
        resolve(port);
      });
    });
  });
}


export function releaseReservedPort(port) {
  const normalizedPort = Number(port);
  const reservation = reservedPortLocks.get(normalizedPort);
  if (!reservation) {
    return;
  }
  reservedPortLocks.delete(normalizedPort);
  reservedPorts.delete(normalizedPort);
  try {
    closeSync(reservation.descriptor);
  } catch {
    // The process may already be tearing down.
  }
  try {
    unlinkSync(reservation.path);
  } catch {
    // A missing lock is already released.
  }
}


function hashRegularFile(path) {
  const descriptor = openSync(path, "r");
  const digest = createHash("sha256");
  const buffer = Buffer.allocUnsafe(1024 * 1024);
  try {
    let count;
    while ((count = readSync(descriptor, buffer, 0, buffer.length, null)) > 0) {
      digest.update(buffer.subarray(0, count));
    }
  } finally {
    closeSync(descriptor);
  }
  return digest.digest("hex");
}


function isExcludedPhase7Asset(relativePath) {
  return relativePath === PHASE7_BUILD_CONTRACT_FILENAME;
}


export function collectPhase7AssetRecords(frontendDirectory) {
  const distDirectory = resolve(frontendDirectory, "dist");
  const records = [];
  const visit = (directory) => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const absolutePath = join(directory, entry.name);
      const relativePath = relative(distDirectory, absolutePath).split(sep).join("/");
      const metadata = lstatSync(absolutePath);
      if (metadata.isSymbolicLink()) {
        throw new Error(`The phase7 dist contains an unsafe symlink: ${relativePath}`);
      }
      if (metadata.isDirectory()) {
        if (!isExcludedPhase7Asset(`${relativePath}/`)) visit(absolutePath);
        continue;
      }
      if (isExcludedPhase7Asset(relativePath)) continue;
      if (!metadata.isFile()) {
        throw new Error(`The phase7 dist contains a non-regular entry: ${relativePath}`);
      }
      records.push({
        relative_path: relativePath,
        size_bytes: metadata.size,
        sha256: hashRegularFile(absolutePath),
      });
    }
  };
  visit(distDirectory);
  records.sort((left, right) => Buffer.compare(
    Buffer.from(left.relative_path),
    Buffer.from(right.relative_path),
  ));
  return records;
}


export function createPhase7BuildContract(frontendDirectory) {
  const records = collectPhase7AssetRecords(frontendDirectory);
  const aggregate = createHash("sha256");
  for (const record of records) {
    aggregate.update(
      `${record.relative_path}\0${record.size_bytes}\0${record.sha256}\n`,
      "utf8",
    );
  }
  const index = records.find((record) => record.relative_path === "index.html");
  if (!index) {
    throw new Error("The phase7 dist is missing index.html.");
  }
  return {
    ...PHASE7_BUILD_CONTRACT,
    asset_count: records.length,
    asset_manifest_sha256: aggregate.digest("hex"),
    index_html_sha256: index.sha256,
  };
}


export function verifyPhase7BuildContract(frontendDirectory) {
  const contractPath = resolve(
    frontendDirectory,
    "dist",
    PHASE7_BUILD_CONTRACT_FILENAME,
  );
  let parsed;
  try {
    parsed = JSON.parse(readFileSync(contractPath, "utf8"));
  } catch {
    throw new Error(
      "The phase7 production build contract is missing or invalid. "
      + "Run: node scripts/build-phase7-production.mjs",
    );
  }
  const expected = createPhase7BuildContract(frontendDirectory);
  if (JSON.stringify(parsed) !== JSON.stringify(expected)) {
    throw new Error(
      "The existing dist does not match its required phase7 production asset contract. "
      + "Run: node scripts/build-phase7-production.mjs",
    );
  }
  const mode = statSync(contractPath).mode & 0o777;
  if (mode !== 0o644) {
    throw new Error("The phase7 production build contract must use mode 0644.");
  }
  return contractPath;
}


export function normalizeRegisteredLoopbackOrigin(rawOrigin) {
  const url = new URL(rawOrigin);
  const hostname = url.hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (
    !["http:", "https:", "ws:", "wss:"].includes(url.protocol)
    || !["127.0.0.1", "::1"].includes(hostname)
    || url.username
    || url.password
    || url.pathname !== "/"
    || url.search
    || url.hash
  ) {
    throw new Error("Only exact numeric-loopback origins may be registered.");
  }
  return url.origin;
}


export function classifyProxyTarget(
  rawUrl,
  { upgrade = false, allowedOrigins = new Set() } = {},
) {
  const url = new URL(rawUrl);
  let protocol = url.protocol;
  if (upgrade && protocol === "http:") protocol = "ws:";
  if (upgrade && protocol === "https:") protocol = "wss:";
  const normalizedTarget = new URL(url.href);
  normalizedTarget.protocol = protocol;
  const origin = normalizedTarget.origin;
  let normalizedOrigin = null;
  try {
    normalizedOrigin = normalizeRegisteredLoopbackOrigin(origin);
  } catch {
    // The diagnostic below is deliberately limited to origin and path identity.
  }
  const allowed = normalizedOrigin !== null && allowedOrigins.has(normalizedOrigin);
  return {
    allowed,
    diagnostic: allowed ? null : {
      scheme: protocol.replace(/:$/, ""),
      origin,
      pathname_hash: createHash("sha256")
        .update(url.pathname)
        .digest("hex")
        .slice(0, 12),
    },
  };
}


export function createNetworkGuardControlToken() {
  return randomBytes(32).toString("hex");
}


function connectAuthorityForOrigin(origin) {
  const url = new URL(origin);
  if (!["https:", "wss:"].includes(url.protocol)) {
    return null;
  }
  const hostname = url.hostname.toLowerCase().replace(/^\[|\]$/g, "");
  const port = Number(url.port || 443);
  return `${hostname}:${port}`;
}


function parseConnectAuthority(authority) {
  const rawAuthority = String(authority || "");
  const ipv4Match = rawAuthority.match(/^127\.0\.0\.1:(\d{1,5})$/);
  const ipv6Match = rawAuthority.match(/^\[::1\]:(\d{1,5})$/);
  const match = ipv4Match || ipv6Match;
  if (!match) {
    return null;
  }
  const hostname = ipv4Match ? "127.0.0.1" : "::1";
  const port = Number(match[1]);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    return null;
  }
  return { hostname, port, key: `${hostname}:${port}` };
}


function sanitizeConnectDiagnosticAuthority(authority) {
  const rawAuthority = String(authority || "").split(/[/?#]/, 1)[0];
  try {
    const parsed = new URL(`https://${rawAuthority}/`);
    const hostname = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, "");
    const port = Number(parsed.port || 443);
    if (!hostname || !Number.isInteger(port) || port < 1 || port > 65535) {
      return "invalid";
    }
    return hostname === "::1" ? `[::1]:${port}` : `${hostname}:${port}`;
  } catch {
    return "invalid";
  }
}


export function classifyConnectAuthority(
  authority,
  { allowedConnectAuthorities = new Map() } = {},
) {
  const parsed = parseConnectAuthority(authority);
  const allowed = parsed !== null
    && Number(allowedConnectAuthorities.get(parsed.key) || 0) > 0;
  const diagnosticAuthority = parsed
    ? parsed.key
    : sanitizeConnectDiagnosticAuthority(authority);
  return {
    allowed,
    parsed,
    diagnostic: allowed ? null : {
      scheme: "connect",
      origin: `connect://${diagnosticAuthority}`,
      pathname_hash: createHash("sha256").update("/").digest("hex").slice(0, 12),
    },
  };
}


export async function startNetworkGuardFixtureServer() {
  const sockets = new Set();
  const trackSocket = (socket) => {
    if (sockets.has(socket)) return;
    sockets.add(socket);
    socket.once("close", () => sockets.delete(socket));
  };
  const page = "<!doctype html><html><head><meta charset=\"utf-8\"><title>Network guard fixture</title></head><body></body></html>\n";
  const serviceWorker = [
    'self.addEventListener("install", () => self.skipWaiting());',
    'self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));',
    'self.addEventListener("message", async (event) => {',
    "  try {",
    '    const target = event.data?.url || "http://elvern-guard-test.invalid/service-worker-probe?token=hidden";',
    "    await fetch(target);",
    "    event.source.postMessage({ blocked: false });",
    "  } catch {",
    "    event.source.postMessage({ blocked: true });",
    "  }",
    "});",
    "",
  ].join("\n");
  const server = http.createServer((request, response) => {
    const pathname = new URL(request.url || "/", "http://127.0.0.1").pathname;
    if (pathname === "/" || pathname === "/index.html") {
      response.writeHead(200, {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
      });
      response.end(page);
      return;
    }
    if (pathname === "/service-worker.js") {
      response.writeHead(200, {
        "Content-Type": "text/javascript; charset=utf-8",
        "Cache-Control": "no-store",
        "Service-Worker-Allowed": "/",
      });
      response.end(serviceWorker);
      return;
    }
    response.writeHead(404, { "Cache-Control": "no-store" });
    response.end();
  });
  server.on("connection", (socket) => {
    trackSocket(socket);
  });
  await new Promise((resolvePromise, rejectPromise) => {
    server.once("error", rejectPromise);
    server.listen(0, "127.0.0.1", resolvePromise);
  });
  const address = server.address();
  const port = typeof address === "object" && address ? Number(address.port) : 0;
  if (!port) {
    server.close();
    throw new Error("Network guard fixture server did not reserve a port.");
  }
  let closePromise = null;
  const close = () => {
    if (!closePromise) {
      closePromise = new Promise((resolvePromise) => {
        for (const socket of sockets) socket.destroy();
        if (!server.listening) {
          resolvePromise();
          return;
        }
        server.close(() => resolvePromise());
      });
    }
    return closePromise;
  };
  return {
    origin: `http://127.0.0.1:${port}`,
    close,
  };
}


export async function startTlsWebSocketFixtureServer({ certificatePath, keyPath }) {
  const sockets = new Set();
  const trackSocket = (socket) => {
    if (sockets.has(socket)) return;
    sockets.add(socket);
    socket.once("close", () => sockets.delete(socket));
  };
  const server = https.createServer({
    cert: readFileSync(certificatePath),
    key: readFileSync(keyPath),
  }, (_request, response) => {
    response.writeHead(200, {
      "Access-Control-Allow-Origin": "*",
      "Cache-Control": "no-store",
      "Content-Type": "text/plain; charset=utf-8",
    });
    response.end("ok\n");
  });
  server.on("connection", (socket) => {
    trackSocket(socket);
  });
  server.on("upgrade", (request, socket) => {
    const websocketKey = String(request.headers["sec-websocket-key"] || "");
    if (!websocketKey) {
      socket.destroy();
      return;
    }
    const accept = createHash("sha1")
      .update(`${websocketKey}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`)
      .digest("base64");
    socket.write([
      "HTTP/1.1 101 Switching Protocols",
      "Upgrade: websocket",
      "Connection: Upgrade",
      `Sec-WebSocket-Accept: ${accept}`,
      "",
      "",
    ].join("\r\n"));
  });
  await new Promise((resolvePromise, rejectPromise) => {
    server.once("error", rejectPromise);
    server.listen(0, "127.0.0.1", resolvePromise);
  });
  const address = server.address();
  const port = typeof address === "object" && address ? Number(address.port) : 0;
  if (!port) {
    server.close();
    throw new Error("TLS WebSocket fixture server did not reserve a port.");
  }
  let closePromise = null;
  const close = () => {
    if (!closePromise) {
      closePromise = new Promise((resolvePromise) => {
        for (const socket of sockets) socket.destroy();
        if (!server.listening) {
          resolvePromise();
          return;
        }
        server.close(() => resolvePromise());
      });
    }
    return closePromise;
  };
  return {
    origin: `wss://127.0.0.1:${port}`,
    httpsOrigin: `https://127.0.0.1:${port}`,
    port,
    close,
  };
}


export async function startLoopbackOnlyProxy({
  initialAllowedOrigins = [],
  controlToken = createNetworkGuardControlToken(),
} = {}) {
  const attempts = [];
  const sockets = new Set();
  const trackSocket = (socket) => {
    if (sockets.has(socket)) return;
    sockets.add(socket);
    socket.once("close", () => sockets.delete(socket));
  };
  const initialOrigins = new Set(
    initialAllowedOrigins.map(normalizeRegisteredLoopbackOrigin),
  );
  const allowedOrigins = new Set(initialOrigins);
  const allowedConnectAuthorities = new Map();
  const addConnectAuthority = (origin) => {
    const authority = connectAuthorityForOrigin(origin);
    if (!authority) return;
    allowedConnectAuthorities.set(
      authority,
      Number(allowedConnectAuthorities.get(authority) || 0) + 1,
    );
  };
  const removeConnectAuthority = (origin) => {
    const authority = connectAuthorityForOrigin(origin);
    if (!authority) return;
    const nextCount = Number(allowedConnectAuthorities.get(authority) || 0) - 1;
    if (nextCount > 0) allowedConnectAuthorities.set(authority, nextCount);
    else allowedConnectAuthorities.delete(authority);
  };
  for (const origin of initialOrigins) addConnectAuthority(origin);
  const authorizedControlRequest = (request) => (
    typeof request.headers[NETWORK_GUARD_CONTROL_HEADER] === "string"
    && request.headers[NETWORK_GUARD_CONTROL_HEADER] === controlToken
  );
  const sendJson = (response, status, payload) => {
    response.writeHead(status, {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    });
    response.end(JSON.stringify(payload));
  };
  const readControlJson = (request, response, callback) => {
    const chunks = [];
    let size = 0;
    request.on("data", (chunk) => {
      size += chunk.length;
      if (size > 4096) {
        response.writeHead(413, { "Cache-Control": "no-store" });
        response.end();
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.on("end", () => {
      if (response.writableEnded) return;
      try {
        callback(JSON.parse(Buffer.concat(chunks).toString("utf8")));
      } catch {
        response.writeHead(400, { "Cache-Control": "no-store" });
        response.end();
      }
    });
  };
  const server = http.createServer((request, response) => {
    const controlPath = String(request.url || "");
    if (controlPath.startsWith("/__elvern_network_guard_")) {
      if (!authorizedControlRequest(request)) {
        response.writeHead(403, { "Cache-Control": "no-store" });
        response.end();
        return;
      }
      if (controlPath === "/__elvern_network_guard_state" && request.method === "GET") {
        sendJson(response, 200, {
          attempts,
          allowed_origins: [...allowedOrigins].sort(),
          allowed_connect_authorities: [...allowedConnectAuthorities.keys()].sort(),
          initial_allowed_origins: [...initialOrigins].sort(),
        });
        return;
      }
      if (controlPath === "/__elvern_network_guard_clear" && request.method === "POST") {
        attempts.length = 0;
        allowedOrigins.clear();
        allowedConnectAuthorities.clear();
        for (const origin of initialOrigins) {
          allowedOrigins.add(origin);
          addConnectAuthority(origin);
        }
        response.writeHead(204, { "Cache-Control": "no-store" });
        response.end();
        return;
      }
      if (
        ["/__elvern_network_guard_register", "/__elvern_network_guard_unregister"]
          .includes(controlPath)
        && request.method === "POST"
      ) {
        readControlJson(request, response, (payload) => {
          const origin = normalizeRegisteredLoopbackOrigin(payload?.origin);
          if (controlPath.endsWith("_register")) {
            if (!allowedOrigins.has(origin)) {
              allowedOrigins.add(origin);
              addConnectAuthority(origin);
            }
          } else if (!initialOrigins.has(origin) && allowedOrigins.has(origin)) {
            allowedOrigins.delete(origin);
            removeConnectAuthority(origin);
          }
          sendJson(response, 200, { origin });
        });
        return;
      }
      response.writeHead(404, { "Cache-Control": "no-store" });
      response.end();
      return;
    }
    let target;
    try {
      target = new URL(request.url);
    } catch {
      response.writeHead(400);
      response.end();
      return;
    }
    const classification = classifyProxyTarget(target.href, {
      upgrade: String(request.headers.upgrade || "").toLowerCase() === "websocket",
      allowedOrigins,
    });
    if (!classification.allowed) {
      attempts.push(classification.diagnostic);
      response.writeHead(403, { "Cache-Control": "no-store" });
      response.end();
      return;
    }
    const upstream = http.request(target, {
      method: request.method,
      headers: request.headers,
    }, (upstreamResponse) => {
      response.writeHead(upstreamResponse.statusCode || 502, upstreamResponse.headers);
      const abortDownstream = () => response.destroy();
      upstreamResponse.once("aborted", abortDownstream);
      upstreamResponse.once("error", abortDownstream);
      upstreamResponse.pipe(response);
    });
    upstream.on("socket", (socket) => {
      trackSocket(socket);
    });
    upstream.on("error", () => {
      if (!response.headersSent) response.writeHead(502);
      response.end();
    });
    request.pipe(upstream);
  });
  server.on("connection", (socket) => {
    trackSocket(socket);
  });

  server.on("connect", (request, clientSocket, head) => {
    const authority = String(request.url || "");
    const classification = classifyConnectAuthority(authority, {
      allowedConnectAuthorities,
    });
    if (!classification.allowed || !classification.parsed) {
      attempts.push(classification.diagnostic);
      clientSocket.end("HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n");
      return;
    }
    const { hostname: host, port } = classification.parsed;
    const upstream = net.connect(port, host, () => {
      clientSocket.write("HTTP/1.1 200 Connection Established\r\n\r\n");
      if (head.length) upstream.write(head);
      upstream.pipe(clientSocket);
      clientSocket.pipe(upstream);
    });
    trackSocket(upstream);
    upstream.on("error", () => clientSocket.destroy());
  });

  server.on("upgrade", (request, socket, head) => {
    let target;
    try {
      target = new URL(request.url);
    } catch {
      socket.destroy();
      return;
    }
    const classification = classifyProxyTarget(target.href, {
      upgrade: true,
      allowedOrigins,
    });
    if (!classification.allowed) {
      attempts.push(classification.diagnostic);
      socket.end("HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n");
      return;
    }
    const host = target.hostname.replace(/^\[|\]$/g, "");
    const port = Number(target.port || 80);
    const upstream = net.connect(port, host, () => {
      upstream.write(
        `${request.method} ${target.pathname}${target.search} HTTP/${request.httpVersion}\r\n`,
      );
      for (const [name, value] of Object.entries(request.headers)) {
        if (value !== undefined && name.toLowerCase() !== "proxy-connection") {
          upstream.write(`${name}: ${value}\r\n`);
        }
      }
      upstream.write("\r\n");
      if (head.length) upstream.write(head);
      upstream.pipe(socket);
      socket.pipe(upstream);
    });
    trackSocket(upstream);
    upstream.on("error", () => socket.destroy());
  });

  await new Promise((resolvePromise, rejectPromise) => {
    server.once("error", rejectPromise);
    server.listen(0, "127.0.0.1", resolvePromise);
  });
  const address = server.address();
  const port = typeof address === "object" && address ? Number(address.port) : 0;
  if (!port) {
    server.close();
    throw new Error("Loopback-only browser proxy did not reserve a port.");
  }
  let closePromise = null;
  const close = () => {
    if (!closePromise) {
      closePromise = new Promise((resolvePromise) => {
        for (const socket of sockets) socket.destroy();
        if (!server.listening) {
          resolvePromise();
          return;
        }
        server.close(() => resolvePromise());
      });
    }
    return closePromise;
  };
  return {
    port,
    attempts,
    allowedOrigins,
    allowedConnectAuthorities,
    controlToken,
    close,
  };
}
