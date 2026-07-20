import { randomBytes } from "node:crypto";
import { closeSync, mkdirSync, openSync, unlinkSync } from "node:fs";
import net from "node:net";
import { tmpdir } from "node:os";
import { join } from "node:path";


const BASE32_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789";
const reservedPorts = new Set();
const reservedPortLocks = new Map();
const lockDirectory = join(tmpdir(), "elvern-playwright-ports");


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
