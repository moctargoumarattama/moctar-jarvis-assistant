'use strict';

const SW_VERSION = 'v11.1';

// Installation metadata only: pages, scripts, telemetry and API responses are never cached.
self.addEventListener('install', event => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    // Delete dashboard caches and purge any stale .js or /api/voice/* entries from CacheStorage (v4.0).
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
              .filter(req => {
                const pathname = new URL(req.url).pathname;
                return pathname.endsWith('.js') || pathname.endsWith('.css') || pathname.startsWith('/api/voice/');
              })
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
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  // Bypasser totalement le Service Worker pour la voix et les flux SSE/WebSocket
  if (url.pathname.startsWith('/api/voice/')) return;
  // Let the native connection stream SSE without an intermediary response.
  if ((request.headers.get('accept') || '').includes('text/event-stream')) return;
  event.respondWith(fetch(request, {cache: 'no-store'}));
});
