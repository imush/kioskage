# kioskage

A self-contained digital-signage appliance: a small PC that boots straight into
a full-screen Chromium kiosk pointed at a web page you choose, with an on-screen
setup flow (no laptop needed) and unattended, self-healing updates. Built on
FreeBSD; designed to be flashed onto cheap mini-PCs and left on a wall.

**The content is the product; the stick is just the delivery mechanism.**
kioskage is the delivery mechanism, open-sourced — bring your own content.

> **[kioskage.org](https://kioskage.org)** · Running in production. The appliance
> is fully de-branded behind `etc/brand.conf` — bring a private overlay for your
> fleet. Questions: [support@kioskage.org](mailto:support@kioskage.org).

## What's here

| Path | What |
|------|------|
| `js/`        | **Content-side tools** — drop-in offline service worker, change-only reloads, and fleet keep-alive for *any* web page. Independent of the appliance. |
| `example/`   | A complete static demo page using `js/` (localized date + clock + public holidays via a CORS-enabled API). |
| `etc/brand.conf` | All product/URL/password/branding values. The code hard-codes none of it. |
| `portal/`    | The on-screen + web setup portal (Python stdlib) and core control logic. |
| `provision/`, `rc.d/`, `etc/`, `bin/`, `kernel/` | FreeBSD install, services, X/Chromium session, OTA updater, custom kernel. |
| `tools/`     | Fleet helpers (e.g. `kiosk-status.sh` — who's alive, from your access log). |

## Branding: kioskage + an overlay

The public repo is fully generic — a neutral `etc/brand.conf` and a plain logo.
To brand a fleet, keep a small **private overlay** (just your `brand.conf`, a
logo, and optionally a custom portal page) and copy it over the defaults at
install time. A stick's identity is `{kioskage} + {your overlay}`. Nothing
brand-specific lives in the code.

## Content-side tools (use these anywhere)

Even if you never touch the appliance, [`js/`](js/) makes any signage page
well-behaved on an always-on display: it never shows a browser error page,
reloads only when the content actually changes, and lets your server see which
displays are alive. See [`js/README.md`](js/README.md) and try
[`example/`](example/).

## License

BSD 3-Clause — see [LICENSE](LICENSE).
