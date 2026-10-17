/* NarizMart service worker — generated per release. Version: {{ version }}
 * - precaches the static shell (hashed filenames in production) and an offline page
 * - never caches HTML or anything user-specific; navigations fall back to the offline page only when the network is down
 * - a new version waits until the visitor taps "Refresh", so a page is never mixed with scripts from another release */
const VERSION = "{{ version }}";
const STATIC_CACHE = "narizmart-static-" + VERSION;
const MEDIA_CACHE = "narizmart-media";
const MEDIA_LIMIT = 80;
const OFFLINE_URL = "{{ offline_url }}";
const PRECACHE = {{ precache_json|safe }};   // JSON built by the server from static file URLs, never user input
const CONTENT_HASHED = {{ content_hashed }};   // true in production: a changed file always has a new URL, so cache-first is safe

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(STATIC_CACHE).then((cache) => cache.addAll(PRECACHE.concat([OFFLINE_URL]))));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== STATIC_CACHE && k !== MEDIA_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") self.skipWaiting();
});

async function trim(cacheName, limit) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  if (keys.length > limit) await Promise.all(keys.slice(0, keys.length - limit).map((k) => cache.delete(k)));
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;
  if (req.headers.get("HX-Request")) return;                       // HTMX fragments: always live

  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.open(STATIC_CACHE).then(async (cache) => {
        const cached = await cache.match(req);
        if (CONTENT_HASHED && cached) return cached;
        try {
          const res = await fetch(req);
          if (res.ok) cache.put(req, res.clone());
          return res;
        } catch (err) {
          if (cached) return cached;
          throw err;
        }
      })
    );
    return;
  }

  if (url.pathname.startsWith("/media/")) {                        // product photos + resized WebP renditions
    event.respondWith(
      caches.open(MEDIA_CACHE).then(async (cache) => {
        const cached = await cache.match(req);
        const network = fetch(req).then((res) => { if (res.ok) { cache.put(req, res.clone()); trim(MEDIA_CACHE, MEDIA_LIMIT); } return res; }).catch(() => cached);
        return cached || network;
      })
    );
    return;
  }

  if (req.mode === "navigate") {                                   // pages carry per-user data: never cached
    event.respondWith(fetch(req).catch(() => caches.match(OFFLINE_URL)));
  }
});
