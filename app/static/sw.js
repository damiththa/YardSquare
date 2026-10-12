const CACHE_NAME = "yardsquare-walk-v1";
const ASSETS = ["/", "/walk", "/api/v1/yard/yard_default", "/api/v1/catalog/species"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  event.respondWith(
    fetch(event.request)
      .then((resp) => {
        const clone = resp.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone)).catch(() => {});
        return resp;
      })
      .catch(() => caches.match(event.request))
  );
});
