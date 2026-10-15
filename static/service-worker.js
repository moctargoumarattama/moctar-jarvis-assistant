'use strict';

// Installation metadata only: pages, telemetry and API responses are never cached.
self.addEventListener('install', event => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    // Delete only caches reserved for this dashboard, never other applications.
    const ownedPrefixes = ['moctar-noc-', 'jarvis-noc-'];
    const names = await caches.keys();
    await Promise.all(names.filter(name => ownedPrefixes.some(prefix => name.startsWith(prefix)))
      .map(name => caches.delete(name)));
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
