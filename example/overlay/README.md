# Example brand overlay (model private repo)

This directory **models the private overlay repo** you keep per fleet/brand. In
a real deployment it lives in its own **private** git repo — it carries your
setup-hotspot password and content URLs — and the stick pulls it via a
**read-only deploy key**, copying it over kioskage's neutral defaults at
install/OTA time. A stick's identity is `{kioskage (public)} + {this overlay}`.

Contents:
- **`brand.conf`** — product name, content URL + kiosk-key base, keep-alive
  domain, hostname/SSID prefix, setup password. Installed to
  `/usr/local/etc/kioskage-brand.conf`.
- **`logo.png`** (optional) — your portal + landing logo. Drop one here and it's
  copied over kioskage's (logoless) default; omit it and the portal just shows
  your `BRAND_NAME` as text.

This sample points a stick at the content in [`..`](..) (`?key=` becomes the
country profile). To brand your own fleet: copy this into a new **private** repo,
edit `brand.conf`, add a `logo.png`, and register a read-only deploy key on it
(see kioskage's OTA setup).
