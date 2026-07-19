# kioskage example content

A complete, self-contained signage page that shows how to make content an
kioskage stick can display. It renders:

- **Today's date and a live clock**, localized to the browser — no dependencies.
- **Upcoming public holidays** for a country, pulled from a public API, with
  graceful fallback (cached copy, then hidden) so a missing/blocked feed never
  blanks the screen.

…and wires in the [`../js`](../js) tools: the offline service worker,
change-only version polling, and keep-alive query forwarding.

Everything is plain static HTML/JS — no build, no server code.

## Both ends of the loop

- **`index.html` + `config.js` + `sw.js`** — the *content* a stick shows (host
  these on any web server).
- **[`overlay/`](overlay/)** — a **model of the private brand-overlay repo** that
  points a stick at that content: a sample `brand.conf` (and where the logo
  goes). In production this is its own private repo the stick pulls via a deploy
  key. It's the stick-config half of the demo. (The appliance's real neutral
  default is [`../etc/brand.conf`](../etc/brand.conf).)

## Preview it locally

Because the page loads `../js/kioskage.js` and fetches a cross-origin API,
preview it over a static server (not `file://`) from the **repo root**:

```sh
cd kioskage
python3 -m http.server 8000
# then open http://localhost:8000/example/
```

Try the per-kiosk key: `…/example/?key=uk`, `?key=de`, or `?key=clock`.

## Make it yours

- **`config.js`** — switch on `?key=` to pick a profile (title, country). The
  holidays come from [Nager.Date](https://date.nager.at), a free, no-key,
  **CORS-enabled** public-holidays API; point `holidaysUrl` at any source that
  sends `Access-Control-Allow-Origin`, or set it to `""` to drop the panel.
- **Why an API and not an `.ics`?** A browser can only fetch a cross-origin feed
  if it sends CORS headers. Many public calendar feeds (e.g. Google's holiday
  `.ics`) don't, so they can't be read client-side; a CORS-enabled JSON API can.
  If your own feed is an `.ics` on a server you control, just add the header.
- **`version.json`** holds the content token. Change it (and the matching
  `version` in `index.html`) to push a reload to every display within one poll
  interval.
- **`sw.js`** just loads `../js/kioskage-sw.js`; on your own site, copy that
  file to your site root as `sw.js` instead.

## What to copy into your own content

1. `js/kioskage-sw.js` → your site root as `sw.js`
2. `js/kioskage.js` → include it and call `Kioskage.init({...})`
3. A `version.json` (or dynamic endpoint) you bump on deploy

The date/holiday rendering here is just demo content — replace it with whatever
your sign should show.
