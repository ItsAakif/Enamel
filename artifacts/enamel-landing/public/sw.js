/*
 * Enamel app-shell service worker.
 *
 * Scope: cache the application shell and immutable build assets ONLY.
 *
 * It deliberately never touches:
 *   - /api/*   (JSON responses, including patient/clinical data)
 *   - /media/* (uploaded documents and imaging)
 *   - any non-GET request
 *
 * A patient's clinical data must never be readable from Cache Storage, so the
 * fetch handler returns early for those paths and the caches are purged on
 * portal logout (see src/lib/pwa.ts).
 */

const CACHE_NAME = "enamel-shell-v1";
const SHELL_URL = "/index.html";
const PRECACHE = ["/index.html", "/manifest.webmanifest", "/favicon.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) =>
        // allSettled: a single missing optional asset must not fail the install.
        Promise.allSettled(PRECACHE.map((url) => cache.add(new Request(url, { cache: "reload" })))),
      )
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

function isCacheableShellRequest(request, url) {
  if (request.method !== "GET") return false;
  if (url.origin !== self.location.origin) return false;
  // Never cache API responses or patient media: they can contain PHI.
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/media/")) return false;
  return true;
}

async function networkFirstShell(request) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const response = await fetch(request);
    if (response && response.ok) {
      await cache.put(SHELL_URL, response.clone());
    }
    return response;
  } catch (error) {
    const cached = await cache.match(SHELL_URL);
    if (cached) return cached;
    throw error;
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response && response.ok) {
    await cache.put(request, response.clone());
  }
  return response;
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  let url;
  try {
    url = new URL(request.url);
  } catch (error) {
    return;
  }
  if (!isCacheableShellRequest(request, url)) return;

  if (request.mode === "navigate") {
    event.respondWith(networkFirstShell(request));
    return;
  }
  // Immutable, content-hashed production assets only. In development the dev
  // server serves modules from /src and /@vite, which fall through to the
  // network so hot reload keeps working.
  if (url.pathname.startsWith("/assets/")) {
    event.respondWith(cacheFirst(request));
  }
});

self.addEventListener("message", (event) => {
  const data = event.data;
  if (data && data.type === "ENAMEL_CLEAR_CACHES") {
    event.waitUntil(caches.keys().then((keys) => Promise.all(keys.map((key) => caches.delete(key)))));
  }
});
