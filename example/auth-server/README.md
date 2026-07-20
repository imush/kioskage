# Reference AUTH_URL endpoint

A ~60-line stdlib-Python implementation of kioskage's content-validated portal
auth contract (see [`../../docs/AUTH.md`](../../docs/AUTH.md)). Copy it as a
starting point; swap the demo store for your real user/password system.

## Run it

```sh
python3 kiosk-auth.py            # listens on 127.0.0.1:8090 (PORT to change)
```

Point a stick at it by setting `AUTH_URL="http://<host>:8090"` in that fleet's
`brand.conf`.

## Try the contract

```sh
# validate a key's password -> ok + PHC hashes the stick caches
curl -s localhost:8090 -d '{"key":"oyyshul","password":"shul-pw"}'
# the master password works for any key
curl -s localhost:8090 -d '{"key":"anything","password":"super-secret-master"}'
# wrong password
curl -s localhost:8090 -d '{"key":"oyyshul","password":"nope"}'
# proactive adopt (no password) -> hashes for the key, so a stick auto-locks
curl -s localhost:8090 -d '{"key":"oyyshul","sync":true}'
```

## Make it real

- Replace `KEYS` / `MASTER_PASSWORD` with your database; store your passwords
  **hashed** server-side (the demo keeps plaintext only to be short).
- Decide whether to keep the `sync` path (hands out a key's hashes without a
  password, so sticks auto-lock on upgrade). The hashes are salted + slow and
  kiosk content is usually public, but drop it if you'd rather require a
  login before a stick caches anything.
- Serve it over HTTPS.
