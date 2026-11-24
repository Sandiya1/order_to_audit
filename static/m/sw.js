// Minimal service worker: keeps the order screens usable with no network.
// Network-first for pages under /m/, falling back to the last cached copy.
const CACHE = 'orders-v1';
self.addEventListener('install', e => { self.skipWaiting(); });
self.addEventListener('activate', e => { e.waitUntil(self.clients.claim()); });
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  const isPage = url.origin === location.origin && url.pathname.startsWith('/m/') && !url.pathname.startsWith('/m/api/');
  const isCdn = url.hostname === 'cdn.jsdelivr.net';
  if (!isPage && !isCdn) return;
  e.respondWith(fetch(req).then(res => {
    const copy = res.clone();
    caches.open(CACHE).then(c => c.put(req, copy));
    return res;
  }).catch(() => caches.match(req)));
});
