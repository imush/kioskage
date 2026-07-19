/*
 * kioskage-sw.js — offline service worker for kioskage content pages.
 *
 * Copy this to your site root as sw.js (service workers only control pages at
 * or below their own path, so it must sit at the root of the content you want
 * covered) and register it via Kioskage.init({ swUrl: "sw.js" }).
 *
 * Behaviour, tuned for an always-on sign that must never show a browser error:
 *   - Page navigations: NETWORK-FIRST. Serve the live page and cache a copy;
 *     if the network is down, serve the last cached page, and if there is none
 *     yet, a small "Reconnecting…" page that retries on its own.
 *   - Same-origin assets (images, css, js): CACHE-FIRST with a background
 *     refresh, so a cold load offline still paints.
 *   - Cross-origin requests are left untouched (your page's own JS decides how
 *     to cache external data such as a calendar feed).
 *
 * Bump CACHE to force old caches to be dropped on the next activation.
 *
 * BSD 3-Clause. Part of https://github.com/ (kioskage).
 */
var CACHE = "kioskage-v1";

self.addEventListener("install", function () { self.skipWaiting(); });

self.addEventListener("activate", function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (k) {
        if (k !== CACHE) { return caches.delete(k); }
      }));
    }).then(function () { return self.clients.claim(); })
  );
});

var OFFLINE_HTML =
  '<!DOCTYPE html><html><head><meta charset="utf-8">' +
  '<meta name="viewport" content="width=device-width,initial-scale=1">' +
  '<title>Reconnecting</title><style>html,body{height:100%;margin:0}' +
  'body{display:flex;align-items:center;justify-content:center;' +
  'background:#0b1020;color:#f4f6fb;font-family:system-ui,sans-serif}' +
  'h1{font-size:2rem;margin:0}</style></head>' +
  '<body><div style="text-align:center"><h1>Reconnecting&hellip;</h1></div>' +
  '<script>setTimeout(function(){location.reload();},15000);</script>' +
  '</body></html>';

self.addEventListener("fetch", function (event) {
  var req = event.request;
  if (req.method !== "GET") { return; }
  var url;
  try { url = new URL(req.url); } catch (e) { return; }
  if (url.origin !== self.location.origin) { return; } // leave cross-origin alone

  // Page navigations: network-first, fall back to last cached page (or the
  // built-in offline page) when the network is unreachable.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).then(function (resp) {
        if (resp && resp.ok) {
          var copy = resp.clone();
          caches.open(CACHE).then(function (c) { c.put(req, copy); });
        }
        return resp;
      }).catch(function () {
        return caches.match(req).then(function (cached) {
          return cached || new Response(OFFLINE_HTML,
            { headers: { "Content-Type": "text/html; charset=utf-8" } });
        });
      })
    );
    return;
  }

  // Same-origin assets: cache-first with background refresh.
  event.respondWith(
    caches.open(CACHE).then(function (c) {
      return c.match(req).then(function (cached) {
        var net = fetch(req).then(function (resp) {
          if (resp && resp.ok) { c.put(req, resp.clone()); }
          return resp;
        }).catch(function () { return cached; });
        return cached || net;
      });
    })
  );
});
