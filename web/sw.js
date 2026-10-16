// Copyright (c) 2026 Woody McLendon
// SPDX-License-Identifier: MIT
/* Service worker: caches the app shell and its vendored libraries so the app opens
 * offline and installs as a PWA. Basemap tiles are never cached: they would grow without
 * bound. Country data packs
 * (*.tlyp.gz) are not cached here either: static.js keeps them in IndexedDB, which this
 * file's activate step (it deletes every cache but the current shell) cannot wipe. */

// Bump SHELL_VERSION on any change to static.js, app.js, style.css or this file, and
// change the ?v= in index.html to match -- tests/test_web_shell.py fails if the two drift
// apart. The query string is what stops a freshly fetched index.html from pairing with a stale cached
// app.js: a mismatched pair once shipped black hexagons and a dead detail toggle with no
// error at all, because the old script read a field the new answers no longer carried.
const SHELL_VERSION = "37";
const CACHE = `tally-shell-v${SHELL_VERSION}`;
const SHELL = [
  "./",
  "./index.html",
  `./static.js?v=${SHELL_VERSION}`,
  `./app.js?v=${SHELL_VERSION}`,
  `./style.css?v=${SHELL_VERSION}`,
  "./manifest.webmanifest",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "./icons/icon-512-maskable.png",
  "./icons/apple-touch-icon.png",
  "./vendor/maplibre-gl@5.24.0/maplibre-gl.css",
  "./vendor/maplibre-gl@5.24.0/maplibre-gl.js",
  "./vendor/h3-js@4.5.0/h3-js.umd.js",
  "./vendor/terra-draw@1.34.0/terra-draw.umd.js",
  "./vendor/terra-draw-maplibre-gl-adapter@1.4.1/terra-draw-maplibre-gl-adapter.umd.js",
  // style.css's typefaces; without them offline the panel falls back to the system font.
  "./vendor/@fontsource/barlow@5.3.0/barlow-latin-400-normal.woff2",
  "./vendor/@fontsource/barlow@5.3.0/barlow-latin-500-normal.woff2",
  "./vendor/@fontsource/barlow@5.3.0/barlow-latin-600-normal.woff2",
  "./vendor/@fontsource/barlow-semi-condensed@5.3.0/barlow-semi-condensed-latin-600-normal.woff2",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((c) => c.addAll(SHELL))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

function isShellRequest(url) {
  if (url.pathname.endsWith(".tlyp.gz")) return false;
  return url.origin === self.location.origin;
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (!isShellRequest(url)) return; // tiles, fonts, packs: straight to the network

  // Network first so edits show up on reload; cache is the offline fallback. "no-cache"
  // makes the browser revalidate with the server instead of trusting its HTTP cache.
  event.respondWith(
    fetch(req, { cache: "no-cache" })
      .then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      })
      // Documents ignore the query string so ?debug= still resolves offline.
      // Assets must match exactly, or the offline fallback would hand back the previous
      // version of app.js and undo the point of versioning it.
      .catch(() => caches.match(req, { ignoreSearch: req.mode === "navigate" })),
  );
});
