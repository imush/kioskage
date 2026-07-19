#!/usr/bin/env python3
"""
Configuration portal HTTP server for the kioskage stick.

Serves the single-page portal UI and a small JSON API on top of kioskagectl.
Stdlib only (http.server) so there are no runtime dependencies beyond the
FreeBSD base python3 package.

Connection attempts run in a background thread so the browser can poll for
progress; this keeps the UI responsive and, crucially, lets a phone that is
connected over the AP survive the moment the AP is torn down on success.
"""

import json
import os
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import kioskagectl as ctl

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PORT = int(os.environ.get("KIOSKAGE_PORT", "80"))
HTTPS_PORT = int(os.environ.get("KIOSKAGE_HTTPS_PORT", "443"))

# Self-signed cert lives in the volatile run dir; regenerated each boot (cheap).
# It exists only so browsers that auto-upgrade http->https reach the portal with
# a click-through warning instead of a confusing connection timeout.
CERT_DIR = os.environ.get("KIOSKAGE_CERT_DIR", "/var/run/kioskage")
CERT_FILE = os.path.join(CERT_DIR, "portal-cert.pem")
KEY_FILE = os.path.join(CERT_DIR, "portal-key.pem")


def ensure_cert():
    """Generate a self-signed cert if one is not already present. Returns True
    if a usable cert/key pair exists afterward."""
    if os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE):
        return True
    os.makedirs(CERT_DIR, exist_ok=True)
    san = "DNS:*.local,DNS:localhost,IP:127.0.0.1,IP:%s" % ctl.AP_ADDR
    rc, _, err = ctl.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", KEY_FILE, "-out", CERT_FILE, "-days", "3650",
        "-subj", "/CN=kioskage-portal", "-addext", "subjectAltName=" + san,
    ], timeout=30)
    if rc != 0:
        print("cert generation failed: %s" % err.strip())
        return False
    os.chmod(KEY_FILE, 0o600)
    return True

# Shared state for the in-flight provisioning attempt.
_attempt = {"state": "idle", "result": None}
_attempt_lock = threading.Lock()

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def _run_attempt(params):
    with _attempt_lock:
        _attempt["state"] = "running"
        _attempt["result"] = None
    try:
        res = ctl.provision(
            mode=params.get("mode", "ethernet"),
            ssid=params.get("ssid"),
            psk=params.get("psk"),
            hostname=params.get("hostname"),
            url=params.get("url"),
            key=params.get("key"),
            auto_start=bool(params.get("auto_start", True)),
        )
    except Exception as e:  # never let the worker die silently
        res = {"ok": False, "reason": "internal error: %s" % e}
    with _attempt_lock:
        _attempt["state"] = "done"
        _attempt["result"] = res


class Handler(BaseHTTPRequestHandler):
    server_version = "kioskage-portal/1.0"

    def log_message(self, fmt, *args):
        pass  # quiet; production logs go to syslog via the service wrapper

    # -- helpers ---------------------------------------------------------
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode())
        except Exception:
            return {}

    def _qr(self):
        # A scannable QR of the address a phone should open to reach the portal.
        ip = ctl.primary_ip() or ctl.AP_ADDR
        target = "http://%s/" % ip
        rc, out, _ = ctl.run(["qrencode", "-t", "SVG", "-m", "2", "-o", "-",
                              target], timeout=10)
        if rc != 0 or not out:
            self.send_response(503)
            self.end_headers()
            return
        body = out.encode() if isinstance(out, str) else out
        self.send_response(200)
        self.send_header("Content-Type", "image/svg+xml")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        safe = os.path.normpath(path).lstrip("/")
        full = os.path.join(STATIC_DIR, safe)
        if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
            # Captive-portal behaviour: unknown paths return the portal page
            full = os.path.join(STATIC_DIR, "index.html")
        ext = os.path.splitext(full)[1]
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type",
                         CONTENT_TYPES.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- routes ----------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/status":
            return self._json(ctl.status())
        if path == "/api/scan":
            return self._json({"networks": ctl.scan_wifi()})
        if path == "/api/connect/status":
            with _attempt_lock:
                return self._json(dict(_attempt))
        if path == "/qr.svg":
            return self._qr()
        if path == "/generate_204" or path == "/hotspot-detect.html":
            # OS captive-portal probes -> force the portal to open
            self.send_response(302)
            self.send_header("Location", "http://%s/" % ctl.AP_ADDR)
            self.end_headers()
            return
        return self._serve_static(path)

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._body_json()
        if path == "/api/connect":
            with _attempt_lock:
                if _attempt["state"] == "running":
                    return self._json({"ok": False,
                                       "reason": "attempt already in progress"},
                                      code=409)
            threading.Thread(target=_run_attempt, args=(body,),
                             daemon=True).start()
            return self._json({"ok": True, "state": "running"})
        if path == "/api/landing/done":
            # The on-screen landing page has finished showing the address/QR and
            # is advancing to content; drop the hold so the next launch is brief.
            ctl.clear_landing_hold()
            return self._json({"ok": True})
        if path == "/api/kiosk/start":
            return self._json(ctl.kiosk_start(body.get("url")))
        if path == "/api/kiosk/stop":
            return self._json(ctl.kiosk_stop())
        if path == "/api/kiosk/restart":
            ctl.kiosk_stop()
            return self._json(ctl.kiosk_start(body.get("url")))
        return self._json({"error": "not found"}, code=404)


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    # Suppress noisy tracebacks from TLS handshake aborts / broken pipes that
    # browsers routinely cause when probing the self-signed HTTPS listener.
    def handle_error(self, request, client_address):
        pass


def main():
    servers = []
    servers.append(("http", QuietThreadingHTTPServer(("0.0.0.0", PORT),
                                                      Handler)))
    if ensure_cert():
        try:
            https_srv = QuietThreadingHTTPServer(("0.0.0.0", HTTPS_PORT),
                                                 Handler)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(CERT_FILE, KEY_FILE)
            https_srv.socket = ctx.wrap_socket(https_srv.socket,
                                               server_side=True)
            servers.append(("https", https_srv))
        except Exception as e:
            print("HTTPS listener disabled: %s" % e)

    threads = []
    for _scheme, srv in servers:
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        threads.append(t)
    where = ", ".join(":%d (%s)" % (srv.server_address[1], scheme)
                      for scheme, srv in servers)
    print("kioskage portal listening on %s (static=%s)" % (where, STATIC_DIR))
    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
