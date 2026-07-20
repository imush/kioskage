# Portal authentication

Once a password is set, the kioskage portal locks **config-changing** actions
(network, content URL, kiosk control) behind it. Read-only status stays open, and
the on-screen setup/landing keep working. **With no credential set the portal is
open** — so an update never locks out an existing stick.

## Two modes (chosen by `AUTH_URL` in `brand.conf`)

**Local-password** (`AUTH_URL` empty) — self-contained, zero server work:
the operator sets a device password via the portal; it's hashed and cached
locally. Good default for generic brands.

**Content-validated** (`AUTH_URL` set) — central credential management: the
portal validates a **kiosk key + password** against your endpoint and caches the
returned hashes for offline use. The key's password and a **master** (super-admin,
valid for any key) both work.

## Credential storage

- Salted **PBKDF2-HMAC-SHA256** in PHC format (`$pbkdf2-sha256$<iters>$<salt>$<hash>`).
- `/usr/local/etc/kioskage-auth`, mode `0600` (root-only; the Chromium user can't read it).
- Two slots: `device` and `master`. Verified constant-time; either matches → unlock.
- Never plaintext, never a bare fast hash (resists offline brute-force if a stick is stolen).

## `AUTH_URL` contract (content-validated)

```
POST <AUTH_URL>  {"key": "...", "password": "..."}
  -> {"ok": true, "creds": {"device": "<phc>", "master": "<phc>"}}   # cached
  -> {"ok": false}

POST <AUTH_URL>  {"key": "...", "sync": true}        # proactive adopt, no password
  -> {"ok": true, "creds": {"device": "<phc>", "master": "<phc>"}}
```

The stick caches `creds` on success; `master` is optional. A reference
implementation is in [`../example/auth-server`](../example/auth-server).

## Behaviours

- **Login** validates against the server (refreshing the cache); **offline it
  falls back to the cached hashes**, so a stick whose network changed is still
  reachable by password. `boot()` calls `sync` to adopt/refresh, so a
  content-validated stick locks itself and can auth offline.
- **Upgrade**: existing passwordless sticks stay open (grandfathered); a
  content-validated stick auto-adopts its key's password on next boot/sync; a
  local-mode stick stays open until an operator sets a password.
- **Changing key X → Y** requires releasing X (its password unlocks the portal)
  and claiming Y (Y's password validated against `AUTH_URL`) — a release+claim
  handshake, so neither hijack direction works remotely.
- **Recovery / bypass**: physical **factory reset** clears all credentials
  (needs no password) — the escape hatch for lost passwords / used sticks.
  Physical access = full control; the lock governs the network path.
- **Transport**: send the password over the portal's **HTTPS** listener on a
  shared LAN; the session cookie is `HttpOnly; SameSite=Strict` (+ `Secure` on HTTPS).
