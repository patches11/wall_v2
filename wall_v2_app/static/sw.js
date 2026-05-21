// Service worker — cache-first for static assets, network-only for /ws and /api.
const CACHE = "wall-v2-v1";
const PRECACHE = ["/", "/static/app.js", "/static/draw.js", "/static/camera.js", "/static/sensors.js", "/static/manifest.json"];

self.addEventListener("install", e =>
    e.waitUntil(caches.open(CACHE).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting()))
);

self.addEventListener("activate", e =>
    e.waitUntil(caches.keys().then(keys =>
        Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim()))
);

self.addEventListener("fetch", e => {
    const url = new URL(e.request.url);
    // Never cache WebSocket upgrades or API calls
    if (url.pathname.startsWith("/ws") || url.pathname.startsWith("/api")) return;
    e.respondWith(
        caches.match(e.request).then(cached => cached || fetch(e.request).then(res => {
            if (res.ok) caches.open(CACHE).then(c => c.put(e.request, res.clone()));
            return res;
        }))
    );
});
