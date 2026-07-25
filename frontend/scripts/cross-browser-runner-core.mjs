import { randomBytes } from "node:crypto";
import { createHash } from "node:crypto";
import {
  closeSync,
  mkdirSync,
  openSync,
  readFileSync,
  unlinkSync,
} from "node:fs";
import http from "node:http";
import net from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";


const BASE32_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789";
const reservedPorts = new Set();
const reservedPortLocks = new Map();
const lockDirectory = join(tmpdir(), "elvern-playwright-ports");
export const PHASE7_BUILD_CONTRACT = Object.freeze({
  schema_version: "elvern-frontend-build-contract-v1",
  library_summary_v2_mode: "on",
  library_revision_mode: "on",
  build_kind: "phase7-production",
});


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


export function verifyPhase7BuildContract(frontendDirectory) {
  const contractPath = resolve(
    frontendDirectory,
    "dist",
    ".elvern-build-contract.json",
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
  if (JSON.stringify(parsed) !== JSON.stringify(PHASE7_BUILD_CONTRACT)) {
    throw new Error(
      "The existing dist was not built with the required phase7 production modes. "
      + "Run: node scripts/build-phase7-production.mjs",
    );
  }
  return contractPath;
}


export function classifyProxyTarget(rawUrl, { upgrade = false } = {}) {
  const url = new URL(rawUrl);
  const hostname = url.hostname.toLowerCase();
  const loopback = hostname === "localhost"
    || hostname === "127.0.0.1"
    || hostname === "::1"
    || hostname === "[::1]";
  return {
    allowed: loopback,
    diagnostic: loopback ? null : {
      scheme: upgrade && url.protocol === "http:" ? "ws" : url.protocol.replace(/:$/, ""),
      origin: url.origin,
      pathname_hash: createHash("sha256")
        .update(url.pathname)
        .digest("hex")
        .slice(0, 12),
    },
  };
}


export async function startLoopbackOnlyProxy() {
  const attempts = [];
  const server = http.createServer((request, response) => {
    if (
      request.url === "/__elvern_network_guard_state"
      && request.headers.host?.startsWith("127.0.0.1:")
    ) {
      response.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
      response.end(JSON.stringify({ attempts }));
      return;
    }
    if (
      request.url === "/__elvern_network_guard_clear"
      && request.headers.host?.startsWith("127.0.0.1:")
    ) {
      attempts.length = 0;
      response.writeHead(204, { "Cache-Control": "no-store" });
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
    upstream.on("error", () => {
      if (!response.headersSent) response.writeHead(502);
      response.end();
    });
    request.pipe(upstream);
  });

  server.on("connect", (request, clientSocket, head) => {
    const authority = String(request.url || "");
    const separator = authority.lastIndexOf(":");
    const rawHost = separator > 0 ? authority.slice(0, separator) : authority;
    const host = rawHost.replace(/^\[|\]$/g, "");
    const port = Number(separator > 0 ? authority.slice(separator + 1) : 443);
    const classification = classifyProxyTarget(`https://${host}:${port}/`);
    if (!classification.allowed) {
      attempts.push(classification.diagnostic);
      clientSocket.end("HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n");
      return;
    }
    const upstream = net.connect(port, host, () => {
      clientSocket.write("HTTP/1.1 200 Connection Established\r\n\r\n");
      if (head.length) upstream.write(head);
      upstream.pipe(clientSocket);
      clientSocket.pipe(upstream);
    });
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
    const classification = classifyProxyTarget(target.href, { upgrade: true });
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
  return {
    port,
    attempts,
    close: () => new Promise((resolvePromise) => server.close(resolvePromise)),
  };
}
