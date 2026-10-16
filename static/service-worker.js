'use strict';

const SW_VERSION = 'v3.1';

// Installation metadata only: pages, scripts, telemetry and API responses are never cached.
self.addEventListener('install', event => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    // Delete dashboard caches and purge any stale .js entries from CacheStorage (v3.1).
    const ownedPrefixes = ['moctar-noc-', 'jarvis-noc-', 'moctar-', 'jarvis-'];
    const names = await caches.keys();
    for (const name of names) {
      if (ownedPrefixes.some(prefix => name.startsWith(prefix))) {
        await caches.delete(name);
      } else {
        try {
          const cache = await caches.open(name);
          const requests = await cache.keys();
          await Promise.all(
            requests
              .filter(req => new URL(req.url).pathname.endsWith('.js'))
              .map(req => cache.delete(req))
          );
        } catch {}
      }
    }
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', event => {
  const request = event.request;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || request.method !== 'GET') return;
  // Let the native connection stream SSE without an intermediary response.
  if ((request.headers.get('accept') || '').includes('text/event-stream')) return;
  event.respondWith(fetch(request, {cache: 'no-store'}));
});
