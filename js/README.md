# kioskage content-side tools

Two tiny, dependency-free files that make any web page a well-behaved kioskage
display. They're independent of the appliance — use them on your own content
server, whatever stack it runs.

| File | Role |
|------|------|
| `kioskage.js`     | Registers the service worker, polls a version token and reloads only on change, and forwards the keep-alive query string. |
| `kioskage-sw.js`  | The offline service worker. Copy it to your **site root** as `sw.js`. |

## Quick start

1. Copy `kioskage-sw.js` to the root of your content site as `sw.js`
   (a service worker only controls pages at or below its own URL path).
2. Add to your content page:

   ```html
   <script src="/kioskage.js"></script>
   <script>
     Kioskage.init({
       version:    "2026-07-17.3",   // bump this string to push a reload
       versionUrl: "/version.json",  // returns the current token as text
       swUrl:      "/sw.js"
     });
   </script>
   ```

3. Serve a `version.json` (or any endpoint) whose body is the current token.
   Overwrite it on each deploy — displays reload within one poll interval
   (default 3 min). Leave it unchanged and the screen never reloads.

## What each piece buys you

- **Offline resilience** — a network outage or browser restart keeps showing the
  last good screen (never the browser's error page), and retries in the
  background.
- **Change-only reloads** — the sign refreshes when *content* changes, not on a
  timer, so scroll positions and animations stay put and an always-on display
  doesn't flicker.
- **Fleet keep-alive** — kioskage sticks append `?host=<name>&v=<build>` to the
  content URL; `kioskage.js` forwards that on every version poll, so your
  access log tells you which sticks are alive without counting anonymous
  visitors. `kioskage`'s `tools/kiosk-status.sh` turns that log into a table.

## Dynamic vs static

`versionUrl` can be a **static file** you overwrite on deploy (simplest — works
on any static host) or a **dynamic endpoint** that also records the keep-alive
hit. The static approach can't log keep-alive itself, but your web server's
access log still captures the `host`/`v` query params.

See [`../example`](../example) for a complete, self-contained page using both
files.
