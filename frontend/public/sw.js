const LEGACY_CACHE_PREFIXES = ["elvern-shell"];

async function clearLegacyElvernCaches() {
  const keys = await caches.keys();
  await Promise.all(
    keys
      .filter((key) => LEGACY_CACHE_PREFIXES.some((prefix) => key.startsWith(prefix)))
      .map((key) => caches.delete(key)),
  );
}


self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});


self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      await clearLegacyElvernCaches();
      await self.clients.claim();
      await self.registration.unregister();
      const clients = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      await Promise.all(clients.map((client) => client.navigate(client.url)));
    })(),
  );
});
