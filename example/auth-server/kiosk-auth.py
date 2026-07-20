#!/usr/bin/env python3
"""
Reference kioskage AUTH_URL endpoint (content-validated portal auth).

A stick with brand.conf AUTH_URL set posts here to validate a kiosk key +
password and to fetch the PHC credential hashes it caches for OFFLINE use.

Contract:
  POST  {"key": "...", "password": "..."}
        -> {"ok": true,  "creds": {"device": "<phc>", "master": "<phc>"}}
        -> {"ok": false}
  POST  {"key": "...", "sync": true}                       # proactive adopt
        -> {"ok": true,  "creds": {"device": "<phc>", "master": "<phc>"}}

Notes:
  - "device" = the key's password hash; "master" = a super-admin credential
    valid for ANY key. The stick caches both and accepts either offline.
  - The hashes are salted, slow PBKDF2 (PHC format) — the same the stick uses.
  - THIS IS A DEMO. Replace the in-memory KEYS / MASTER_PASSWORD with your real
    user store (and store *hashed* passwords server-side, not plaintext). The
    'sync' path hands out a key's hashes without a password; only enable it if
    you're comfortable with that (the hashes are slow+salted, and kiosk content
    is typically public anyway) — otherwise drop it and rely on login-time
    caching.
"""
import base64
import hashlib
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

# --- demo credential store (replace with your database) -------------------
KEYS = {"lobby": "lobby-pw", "oyyshul": "shul-pw"}
MASTER_PASSWORD = "super-secret-master"

PBKDF2_ITERS = 200000


def phc(password, iterations=PBKDF2_ITERS):
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    b64 = lambda x: base64.b64encode(x).decode("ascii")
    return "$pbkdf2-sha256$%d$%s$%s" % (iterations, b64(salt), b64(dk))


def creds_for(key):
    out = {"master": phc(MASTER_PASSWORD)}
    if key in KEYS:
        out["device"] = phc(KEYS[key])
    return out


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            body = {}
        key = body.get("key", "")
        if body.get("sync"):
            ok = key in KEYS
        else:
            pw = body.get("password", "")
            ok = (KEYS.get(key) == pw) or (pw == MASTER_PASSWORD)
        resp = {"ok": ok, "creds": creds_for(key) if ok else {}}
        out = json.dumps(resp).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8090"))
    print("kiosk-auth reference endpoint on http://127.0.0.1:%d" % port)
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
