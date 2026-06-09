// Service worker — network-first for static assets, network-only for /ws and /api.
const CACHE = "wall-v2-v3";
const PRECACHE = ["/", "/static/app.js", "/static/draw.js", "/static/camera.js", "/static/sensors.js", "/static/video.js", "/static/manifest.json"];

self.addEventListener("install", e =>
    e.waitUntil(caches.open(CACHE).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting()))
);

self.addEventListener("activate", e =>
    e.waitUntil(caches.keys().then(keys =>
        Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim()))
);

// Network-first so reloading always picks up the latest code (the server is on
// the LAN and effectively always reachable).  Fall back to cache only offline.
self.addEventListener("fetch", e => {
    const url = new URL(e.request.url);
    // Never cache WebSocket upgrades or API calls
    if (url.pathname.startsWith("/ws") || url.pathname.startsWith("/api")) return;
    e.respondWith(
        fetch(e.request).then(res => {
            if (res.ok) {
                const copy = res.clone();
                caches.open(CACHE).then(c => c.put(e.request, copy));
            }
            return res;
        }).catch(() => caches.match(e.request))
    );
});
