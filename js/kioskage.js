/*
 * kioskage.js — content-side helper for kioskage displays.
 *
 * Drop this into any signage page you want an kioskage stick to show, and call
 * Kioskage.init(). It does three small things, all optional:
 *
 *   1. Registers an offline service worker (see kioskage-sw.js) so a browser
 *      restart or network blip keeps showing the last good screen instead of a
 *      "no internet" error page.
 *
 *   2. Polls a tiny "version token" every few minutes and reloads the page ONLY
 *      when the token changes. An unchanged screen is never reloaded, so the
 *      display stays rock-steady (scroll positions, animations) and a content
 *      edit rolls out within one poll interval.
 *
 *   3. Forwards the page's query string on each version poll. kioskage sticks
 *      append "?host=<name>&v=<build>" to their content URL, so forwarding it
 *      lets your server log which sticks are alive (fleet keep-alive) without
 *      counting anonymous visitors. See tools/kiosk-status.sh in kioskage.
 *
 * BSD 3-Clause. Part of https://github.com/ (kioskage). Vanilla JS, no deps.
 *
 * Usage:
 *   <script src="kioskage.js"></script>
 *   <script>
 *     Kioskage.init({
 *       version:    "2026-07-17.3",   // current content token (bump to reload)
 *       versionUrl: "version.json",   // endpoint returning the current token
 *       pollMs:     180000,           // 3 min (default)
 *       swUrl:      "sw.js"           // omit / set false to skip the SW
 *     });
 *   </script>
 *
 * `versionUrl` may be a static file (e.g. version.json you overwrite on deploy)
 * or a dynamic endpoint. Its body is compared, trimmed, as text against
 * `version`; any difference triggers one location.reload().
 */
(function (global) {
  "use strict";

  function registerSW(swUrl) {
    if (swUrl === false) return;
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.register(swUrl || "sw.js").catch(function () {});
  }

  function startVersionPoll(opts) {
    var current = opts.version;
    if (opts.versionUrl == null || current == null) return; // polling disabled
    var pollMs = opts.pollMs || 180000;
    // Forward the page query string (host/build tags) so the server can log
    // this stick as alive on every poll. Opt out with forwardQuery: false.
    var query = opts.forwardQuery === false ? "" : global.location.search;
    var url = opts.versionUrl + query;
    current = String(current).trim();

    function tick() {
      fetch(url, { cache: "no-store" })
        .then(function (r) { return r.ok ? r.text() : null; })
        .then(function (t) {
          if (t !== null && t.trim() !== current) {
            global.location.reload();
          } else {
            setTimeout(tick, pollMs);
          }
        })
        .catch(function () { setTimeout(tick, pollMs); }); // outage: retry later
    }
    setTimeout(tick, pollMs);
  }

  var Kioskage = {
    init: function (opts) {
      opts = opts || {};
      registerSW(opts.swUrl);
      startVersionPoll(opts);
    }
  };

  if (typeof module !== "undefined" && module.exports) module.exports = Kioskage;
  global.Kioskage = Kioskage;
})(typeof self !== "undefined" ? self : this);
