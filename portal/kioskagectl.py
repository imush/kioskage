#!/usr/bin/env python3
"""
kioskagectl - core control logic for the kioskage stick.

Single source of truth for network, hostname, kiosk and config management.
Used both by the HTTP portal (server.py) and the boot service (rc.d/kioskage).

Target platform: FreeBSD 15.x amd64. Most operations shell out to base-system
tools (ifconfig, wpa_supplicant, dhclient, hostname) and a few packages
(hostapd, dnsmasq, chromium, avahi). Everything is designed to be safe to run
on a read-only root: mutable state lives under /var (tmpfs in production).

The module is import-safe on non-FreeBSD hosts (e.g. a Mac dev box); the
commands simply won't exist, so functions that shell out will report errors
rather than crash the interpreter.
"""

import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time

# --------------------------------------------------------------------------
# Paths / constants
# --------------------------------------------------------------------------

CONF_PATH = os.environ.get("KIOSKAGE_CONF", "/usr/local/etc/kioskage.conf")
# Cached portal credentials (PHC-format PBKDF2 hashes). Root-only; the kiosk
# (Chromium) user must never read it. Holds a "device" credential (local-mode
# password, or the adopted kiosk-key password) and an optional "master".
AUTH_PATH = os.environ.get("KIOSKAGE_AUTH", "/usr/local/etc/kioskage-auth")
RUN_DIR = os.environ.get("KIOSKAGE_RUN", "/var/run/kioskage")
WPA_CONF = os.path.join(RUN_DIR, "wpa_supplicant.conf")
HOSTAPD_CONF = os.path.join(RUN_DIR, "hostapd.conf")
DNSMASQ_CONF = os.path.join(RUN_DIR, "dnsmasq.conf")
# Present while the stick is in on-screen setup mode (unconfigured, or saved
# network unreachable). The kiosk shows the local portal instead of content.
SETUP_FLAG = os.path.join(RUN_DIR, "setup-mode")
# Present just after a (re)provision: tells the on-screen landing page to HOLD
# the address + QR on the TV for a good while (rather than the short boot flash)
# so that after a hotspot->home-network switch — where the configuring phone
# loses the stick — the new IP/QR stays visible long enough to rejoin. Cleared
# once the landing page advances to content, and on every normal boot.
LANDING_HOLD = os.path.join(RUN_DIR, "landing-hold")

STA_IF = "wlan0"        # station (client) wlan clone
AP_IF = "wlan1"         # access-point wlan clone
AP_ADDR = "192.168.4.1"
AP_CIDR = "192.168.4.1/24"

# Brand profile: every product / URL / password / branding value lives in a
# separate brand.conf (see etc/brand.conf) — nothing site-specific is hard-coded
# here. Ship your own brand.conf on top of the neutral default to brand a fleet.
BRAND_PATH = os.environ.get("KIOSKAGE_BRAND",
                            "/usr/local/etc/kioskage-brand.conf")

_BRAND_DEFAULTS = {
    "BRAND_NAME": "Kioskage",
    "ACCENT_COLOR": "#3a6ea5",
    "LOGO": "logo.png",
    "DEFAULT_URL": "about:blank",
    "CONTENT_URL_BASE": "",
    "KEEPALIVE_DOMAIN": "",
    "HOSTNAME_PREFIX": "kiosk",
    "SETUP_SSID": "kiosk",
    "SETUP_PSK": "kiosksetup",
    # Portal auth: when set, the portal validates a kiosk key + password against
    # this endpoint (content-validated mode) and caches the result for offline
    # use. Empty -> local-password mode (the operator sets a device password).
    "AUTH_URL": "",
}


def load_brand():
    """Read brand.conf (sh KEY="value" lines) over the neutral defaults. Only
    known keys are honoured; anything else is ignored."""
    brand = dict(_BRAND_DEFAULTS)
    try:
        with open(BRAND_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                # Take the quoted value (ignoring any trailing inline comment);
                # for an unquoted value strip a trailing comment. Note a '#' can
                # be inside a quoted value, e.g. ACCENT_COLOR="#3a6ea5".
                if v[:1] == '"':
                    v = v[1:]
                    end = v.find('"')
                    if end >= 0:
                        v = v[:end]
                else:
                    v = v.split("#", 1)[0].strip()
                if k in brand:
                    brand[k] = v
    except FileNotFoundError:
        pass
    return brand


BRAND = load_brand()

# Setup-mode hotspot: while unconfigured the stick auto-joins any phone hotspot
# whose PASSWORD is SETUP_PSK — the network name doesn't matter (iPhone hotspots
# are named after the device and can't be set to a fixed SSID). It matches by
# password, trying the strongest secured networks first. SETUP_SSID is only a
# fast-path hint (easy to set on Android).
SETUP_SSID = BRAND["SETUP_SSID"]
SETUP_PSK = BRAND["SETUP_PSK"]
SETUP_POLL = 8          # seconds between setup-mode network attempts
SETUP_ASSOC = 10        # per-network association timeout while password-scanning
SETUP_MAX_TRY = 6       # secured networks to try per scan cycle
SETUP_RETRY_CYCLES = 8  # every N cycles, forget failures and re-try everything

# Content: the portal's "kiosk key" builds CONTENT_URL_BASE + "?key=<key>" (many
# kiosks sharing one content site). Empty base -> the key field is hidden and
# operators paste a full URL. DEFAULT_URL is shown until the stick is configured.
KIOSK_URL_BASE = BRAND["CONTENT_URL_BASE"]
DEFAULT_URL = BRAND["DEFAULT_URL"]
DEFAULT_HOSTNAME_PREFIX = BRAND["HOSTNAME_PREFIX"]


def kiosk_url(key):
    """Build the content URL for a kiosk key. Keys are [a-z0-9-] so no escaping
    is needed; an empty key (or no configured base) yields the plain default."""
    key = (key or "").strip()
    if not KIOSK_URL_BASE:
        return DEFAULT_URL
    return "%s?key=%s" % (KIOSK_URL_BASE, key) if key else KIOSK_URL_BASE


# --------------------------------------------------------------------------
# Portal authentication (credential storage + verification)
# --------------------------------------------------------------------------
#
# The portal locks config-changing actions behind a password once one is set.
# Credentials are stored as salted PBKDF2 hashes in PHC string format under
# AUTH_PATH (root-only) so a stolen stick can't have weak passwords brute-forced
# quickly, and offline validation needs no plaintext.
#
# Two slots: "device" (the local-mode password, or the adopted kiosk-key
# password) and "master" (a super-admin credential pushed by the content server
# in content-validated mode). Verification accepts either. If NEITHER slot is
# set the portal is OPEN (grandfathered) — so an update can never lock anyone
# out of a stick they cannot otherwise reach.

_PBKDF2_ITERS = 200000    # PBKDF2-HMAC-SHA256 rounds for stick-generated hashes


def hash_password(password, iterations=_PBKDF2_ITERS):
    """Return a PHC string: $pbkdf2-sha256$<iters>$<b64salt>$<b64hash>."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    enc = lambda x: base64.b64encode(x).decode("ascii")
    return "$pbkdf2-sha256$%d$%s$%s" % (iterations, enc(salt), enc(dk))


def verify_password(password, phc):
    """Constant-time check of a password against a stored PHC hash."""
    try:
        parts = phc.split("$")          # ["", "pbkdf2-sha256", iters, salt, hash]
        if len(parts) != 5 or parts[1] != "pbkdf2-sha256":
            return False
        salt = base64.b64decode(parts[3])
        expected = base64.b64decode(parts[4])
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 salt, int(parts[2]))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


def load_creds():
    """Read the cached credential slots ({} if none/unreadable)."""
    try:
        with open(AUTH_PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def save_creds(creds):
    """Write credential slots atomically, root-only (0600)."""
    os.makedirs(os.path.dirname(AUTH_PATH), exist_ok=True)
    tmp = AUTH_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(creds, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, AUTH_PATH)


def auth_configured():
    """True if any credential is set (=> the portal is locked). No credential
    means an open, grandfathered portal (never lock out on upgrade)."""
    creds = load_creds()
    return bool(creds.get("device") or creds.get("master"))


def check_password(password):
    """True if the password matches the device OR master credential (offline /
    default local validation)."""
    if not password:
        return False
    creds = load_creds()
    for slot in ("device", "master"):
        phc = creds.get(slot)
        if phc and verify_password(password, phc):
            return True
    return False


def set_device_password(password):
    """Set/replace the device credential (local-password mode, or adopting a
    validated kiosk-key password). Empty password clears it (re-opens)."""
    creds = load_creds()
    if password:
        creds["device"] = hash_password(password)
    else:
        creds.pop("device", None)
    save_creds(creds)


def set_master_credential(phc):
    """Cache the master credential hash pushed by the content server (already a
    PHC string). Falsy clears it."""
    creds = load_creds()
    if phc:
        creds["master"] = phc
    else:
        creds.pop("master", None)
    save_creds(creds)


def clear_creds():
    """Forget all cached credentials (re-opens the portal); used by
    factory_reset so a wiped stick starts open again."""
    try:
        os.remove(AUTH_PATH)
    except FileNotFoundError:
        pass


# --- Content-validated mode (AUTH_URL) ------------------------------------
#
# When BRAND["AUTH_URL"] is set, the portal validates a kiosk key + password
# against that endpoint and caches the returned PHC hashes for offline use. The
# contract (also implemented by example/auth-server):
#   POST <AUTH_URL> {"key","password"} -> {"ok":true,"creds":{"device":"<phc>",
#                                            "master":"<phc>"}} | {"ok":false}
#   POST <AUTH_URL> {"key","sync":true} -> {"ok":true,"creds":{...}}   (adopt)
# 'creds' (returned on ok) is what the stick caches; 'master' is a super-admin
# credential valid for any key. sync lets a stick lock itself + cache offline
# creds without a password (auto-adopt on upgrade).


def _auth_post(payload, timeout=8):
    """POST JSON to AUTH_URL; return the parsed dict, or None on any failure
    (unreachable, timeout, bad response) so callers can fall back to the cache."""
    url = BRAND["AUTH_URL"]
    if not url:
        return None
    try:
        import urllib.request
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def cache_remote_creds(creds):
    """Cache server-provided PHC hashes (device + master) for offline auth."""
    if not isinstance(creds, dict):
        return
    slot = load_creds()
    for name in ("device", "master"):
        if creds.get(name):
            slot[name] = creds[name]
    save_creds(slot)


def remote_auth(key, password):
    """Validate key+password against AUTH_URL. Returns True/False when the server
    answers (caching creds on success), or None when it is unreachable."""
    resp = _auth_post({"key": key or "", "password": password})
    if resp is None:
        return None
    if resp.get("ok"):
        cache_remote_creds(resp.get("creds") or {})
        return True
    return False


def sync_creds():
    """Content mode: pull the current credential hashes for this stick's key
    (no password) so the stick locks itself and can validate offline. No-op in
    local mode or when offline."""
    if not BRAND["AUTH_URL"]:
        return
    resp = _auth_post({"key": load_config().get("KIOSK_KEY", ""), "sync": True})
    if resp and resp.get("ok"):
        cache_remote_creds(resp.get("creds") or {})


def authenticate(password):
    """Portal login check. Content mode: validate against the server (refreshing
    the cache) with an offline fallback to the cached hashes. Local mode: check
    the cached device/master hash."""
    if not password:
        return False
    if BRAND["AUTH_URL"]:
        result = remote_auth(load_config().get("KIOSK_KEY", ""), password)
        if result is not None:
            return result                 # server decided (cached on success)
        # offline -> fall through to the cached hashes
    return check_password(password)

ETH_PREFIXES = ("em", "igb", "igc", "ix", "re", "bge", "alc", "ale",
                "msk", "nfe", "ue", "cdce", "rue", "axge", "mos")

ASSOC_TIMEOUT = 25      # seconds to reach wpa COMPLETED
DHCP_TIMEOUT = 15       # seconds to obtain a lease


# --------------------------------------------------------------------------
# Low-level helpers
# --------------------------------------------------------------------------

# The portal runs from rc.d with a base-only PATH; make sure subprocesses can
# also find pkg-installed tools (qrencode, dnsmasq, ...) in /usr/local.
_RUN_ENV = dict(os.environ)
_RUN_ENV["PATH"] = "/sbin:/bin:/usr/sbin:/usr/bin:/usr/local/sbin:/usr/local/bin"


def run(cmd, timeout=30, check=False):
    """Run a command, return (rc, stdout, stderr). Never raises on non-zero
    unless check=True."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, env=_RUN_ENV)
    except FileNotFoundError:
        return (127, "", "command not found: %s" % cmd[0])
    except subprocess.TimeoutExpired:
        return (124, "", "timeout: %s" % " ".join(cmd))
    if check and p.returncode != 0:
        raise RuntimeError("%s failed: %s" % (" ".join(cmd), p.stderr.strip()))
    return (p.returncode, p.stdout, p.stderr)


def out(cmd, timeout=30):
    """Convenience: return stdout string (empty on failure)."""
    return run(cmd, timeout=timeout)[1]


def sysctl(name):
    return out(["sysctl", "-n", name]).strip()


# --------------------------------------------------------------------------
# Config file (sh-sourceable KEY="value" lines)
# --------------------------------------------------------------------------

def load_config():
    cfg = {}
    try:
        with open(CONF_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"')
    except FileNotFoundError:
        pass
    return cfg


def save_config(cfg):
    os.makedirs(os.path.dirname(CONF_PATH), exist_ok=True)
    tmp = CONF_PATH + ".tmp"
    order = ["CONFIGURED", "NET_MODE", "WIFI_SSID", "WIFI_PSK",
             "CONTENT_URL", "KIOSK_KEY", "HOSTNAME", "AUTO_START"]
    keys = order + [k for k in cfg if k not in order]
    with open(tmp, "w") as f:
        f.write("# kioskage.conf - written by kioskagectl. Do not edit by hand.\n")
        for k in keys:
            if k in cfg and cfg[k] is not None:
                v = str(cfg[k]).replace('"', '\\"')
                f.write('%s="%s"\n' % (k, v))
    # Root-only: the file holds the WiFi passphrase in plaintext and must not
    # be readable by the kioskage (Chromium) user.
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONF_PATH)


# --------------------------------------------------------------------------
# Interface discovery
# --------------------------------------------------------------------------

def all_ifaces():
    return out(["ifconfig", "-l"]).split()


def wlan_devices():
    """Physical wifi-capable devices, e.g. ['run0', 'iwm0']."""
    return sysctl("net.wlan.devices").split()


def ethernet_ifaces():
    return [i for i in all_ifaces() if i.startswith(ETH_PREFIXES)]


def iface_status(dev):
    """Return dict {present, active, ip} for an interface."""
    rc, text, _ = run(["ifconfig", dev])
    if rc != 0:
        return {"present": False, "active": False, "ip": None}
    active = "status: active" in text or "status: associated" in text
    m = re.search(r"\binet (\d+\.\d+\.\d+\.\d+)", text)
    ip = m.group(1) if m else None
    return {"present": True, "active": active, "ip": ip}


def primary_mac():
    """MAC of the first ethernet iface, else first wifi device, colon-free."""
    for dev in ethernet_ifaces() + wlan_devices():
        m = re.search(r"ether ([0-9a-f:]{17})", out(["ifconfig", dev]))
        if m:
            return m.group(1).replace(":", "")
    return None


def has_internet():
    """True if we can reach the wider internet (ping + DNS)."""
    rc, _, _ = run(["ping", "-c", "1", "-t", "3", "8.8.8.8"], timeout=6)
    return rc == 0


def stop_dhclient(iface):
    """Stop a dhclient bound to iface. FreeBSD's dhclient has no -x/-r release
    flag (that's the Linux/ISC client), so kill it by its per-interface pidfile,
    falling back to a command-line match."""
    for pf in ("/var/run/dhclient/dhclient.%s.pid" % iface,
               "/var/run/dhclient.%s.pid" % iface):
        try:
            with open(pf) as f:
                pid = int(f.read().strip())
            run(["kill", str(pid)], timeout=5)
            return
        except (FileNotFoundError, ValueError):
            continue
    run(["pkill", "-f", "dhclient.*%s" % re.escape(iface)], timeout=5)


def clear_dhcp_lease(iface):
    """Delete the cached DHCP lease for iface. FreeBSD's dhclient otherwise
    re-applies a previous network's lease on the next run — so after switching
    from the setup hotspot to the home network the stick would briefly bind the
    hotspot's address (wrong subnet, no gateway) before DHCP corrects it, and
    connect_wifi() would return that stale IP. Removing the lease forces a fresh
    DISCOVER so the first address we see belongs to the network we just joined."""
    for lf in ("/var/db/dhclient.leases.%s" % iface,):
        try:
            os.remove(lf)
        except FileNotFoundError:
            pass


def flush_iface_ip(iface):
    """Remove any IPv4 address currently on iface (so a stale one can't be
    mistaken for a live lease while the next DHCP is still in flight)."""
    ip = iface_status(iface)["ip"]
    if ip:
        run(["ifconfig", iface, "inet", ip, "delete"], timeout=5)


def default_route_iface():
    """The interface the default route currently egresses, or None."""
    ifaces = set(all_ifaces())
    for line in out(["netstat", "-rn", "-f", "inet"]).splitlines():
        f = line.split()
        if f and f[0] == "default":
            for tok in f[1:]:      # Netif is the last known-iface token
                if tok in ifaces:
                    return tok
    return None


# --------------------------------------------------------------------------
# Hostname
# --------------------------------------------------------------------------

def gen_hostname():
    mac = primary_mac()
    if mac:
        return "%s-%s" % (DEFAULT_HOSTNAME_PREFIX, mac[-6:].lower())
    # Fallback: derive 6 stable-ish hex chars from host id / boot time-free
    hid = sysctl("kern.hostuuid") or ""
    digits = re.sub(r"[^0-9a-f]", "", hid.lower())
    suffix = (digits + "000000")[:6] if digits else "000000"
    return "%s-%s" % (DEFAULT_HOSTNAME_PREFIX, suffix)


def set_hostname(name):
    name = re.sub(r"[^a-zA-Z0-9-]", "", name)[:63] or gen_hostname()
    run(["hostname", name])
    # Persist to rc.conf so it survives reboot
    try:
        run(["sysrc", 'hostname=%s' % name])
    except Exception:
        pass
    return name


# --------------------------------------------------------------------------
# WiFi: scanning
# --------------------------------------------------------------------------

_MAC_RE = re.compile(r"([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}")


def ensure_sta_iface():
    """Make sure the station wlan clone exists and is up. Returns iface or
    None if no wifi hardware is present."""
    devs = wlan_devices()
    if not devs:
        return None
    if STA_IF not in all_ifaces():
        run(["ifconfig", STA_IF, "create", "wlandev", devs[0]])
    run(["ifconfig", STA_IF, "up"])
    return STA_IF


def current_ssid():
    """The SSID wlan0 is actually associated with right now, or None."""
    if STA_IF not in all_ifaces():
        return None
    text = out(["ifconfig", STA_IF])
    if "status: associated" not in text and "status: running" not in text:
        return None
    m = re.search(r"\bssid (.+?) channel ", text)
    if not m:
        return None
    return m.group(1).strip().strip('"') or None


def scan_wifi():
    """Return a list of {ssid, signal, secure} dicts, strongest first."""
    iface = ensure_sta_iface()
    if not iface:
        return []
    run(["ifconfig", iface, "scan"], timeout=15)  # trigger active scan
    text = out(["ifconfig", iface, "list", "scan"], timeout=15)
    nets = {}
    for line in text.splitlines()[1:]:  # skip header
        m = _MAC_RE.search(line)
        if not m:
            continue
        ssid = line[:m.start()].strip()
        rest = line[m.end():]
        if not ssid:
            continue  # hidden network
        sig = None
        sn = re.search(r"(-?\d+):(-?\d+)", rest)  # S:N field
        if sn:
            sig = int(sn.group(1))
        secure = bool(re.search(r"\b(RSN|WPA|WPS)\b", rest))
        cur = nets.get(ssid)
        if cur is None or (sig is not None and sig > cur["signal"]):
            nets[ssid] = {"ssid": ssid,
                          "signal": sig if sig is not None else -999,
                          "secure": secure}
    result = sorted(nets.values(), key=lambda n: n["signal"], reverse=True)
    for n in result:
        if n["signal"] == -999:
            n["signal"] = None
    return result


# --------------------------------------------------------------------------
# WiFi: connecting (STA mode)
# --------------------------------------------------------------------------

def _write_wpa_conf(ssid, psk):
    os.makedirs(RUN_DIR, exist_ok=True)
    lines = ["ctrl_interface=/var/run/wpa_supplicant", "", "network={",
             '\tssid="%s"' % ssid, "\tscan_ssid=1"]
    if psk:
        lines.append('\tpsk="%s"' % psk)
    else:
        lines.append("\tkey_mgmt=NONE")
    lines.append("}")
    with open(WPA_CONF, "w") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(WPA_CONF, 0o600)


def _wpa_state(iface):
    text = out(["wpa_cli", "-i", iface, "status"])
    m = re.search(r"wpa_state=(\w+)", text)
    return m.group(1) if m else ""


def connect_wifi(ssid, psk, assoc_timeout=None):
    """Attempt to associate + DHCP on the given network.
    Returns dict {ok, reason, ip}."""
    iface = ensure_sta_iface()
    if not iface:
        return {"ok": False, "reason": "no wifi hardware detected", "ip": None}

    # Tear down any previous supplicant, lease and address on this iface so we
    # don't carry a prior network's state (esp. a stale DHCP lease) onto the new
    # one — otherwise switching from the setup hotspot leaves the stick with the
    # hotspot's address on the home network.
    run(["wpa_cli", "-i", iface, "terminate"], timeout=5)
    stop_dhclient(iface)
    flush_iface_ip(iface)
    clear_dhcp_lease(iface)
    time.sleep(1)

    _write_wpa_conf(ssid, psk)
    rc, _, err = run(["wpa_supplicant", "-B", "-i", iface, "-c", WPA_CONF],
                     timeout=10)
    if rc != 0:
        return {"ok": False, "reason": "wpa_supplicant failed: %s" % err.strip(),
                "ip": None}

    # Wait for association
    deadline = time.time() + (assoc_timeout or ASSOC_TIMEOUT)
    state = ""
    while time.time() < deadline:
        state = _wpa_state(iface)
        if state == "COMPLETED":
            break
        if state == "4WAY_HANDSHAKE" and psk:
            # lingering here usually means a bad passphrase
            pass
        time.sleep(1)
    if state != "COMPLETED":
        run(["wpa_cli", "-i", iface, "terminate"], timeout=5)
        reason = ("wrong password or network out of range"
                  if psk else "could not join network (out of range?)")
        return {"ok": False, "reason": reason, "ip": None}

    # DHCP
    run(["dhclient", "-b", iface], timeout=DHCP_TIMEOUT + 2)
    deadline = time.time() + DHCP_TIMEOUT
    ip = None
    while time.time() < deadline:
        ip = iface_status(iface)["ip"]
        if ip:
            break
        time.sleep(1)
    if not ip:
        run(["wpa_cli", "-i", iface, "terminate"], timeout=5)
        return {"ok": False, "reason": "joined network but no IP (DHCP failed)",
                "ip": None}

    return {"ok": True, "reason": "connected", "ip": ip}


def connect_ethernet():
    """Bring up the first ethernet iface via DHCP. Returns {ok, reason, ip}."""
    ifaces = ethernet_ifaces()
    if not ifaces:
        return {"ok": False, "reason": "no ethernet interface found", "ip": None}
    dev = ifaces[0]
    run(["ifconfig", dev, "up"])
    st = iface_status(dev)
    if not st["active"]:
        return {"ok": False, "reason": "ethernet cable not connected", "ip": None}
    if not st["ip"]:
        run(["dhclient", "-b", dev], timeout=DHCP_TIMEOUT + 2)
        deadline = time.time() + DHCP_TIMEOUT
        while time.time() < deadline:
            st = iface_status(dev)
            if st["ip"]:
                break
            time.sleep(1)
    if not st["ip"]:
        return {"ok": False, "reason": "ethernet up but no IP (DHCP failed)",
                "ip": None}
    return {"ok": True, "reason": "connected", "ip": st["ip"]}


# --------------------------------------------------------------------------
# Access Point mode (captive portal for shipped, no-ethernet units)
# --------------------------------------------------------------------------

def ensure_ap_iface():
    devs = wlan_devices()
    if not devs:
        return None
    ap_dev = devs[-1]  # prefer the last device (typically the USB dongle)
    if AP_IF not in all_ifaces():
        run(["ifconfig", AP_IF, "create", "wlandev", ap_dev, "wlanmode",
             "hostap"])
    return AP_IF


def ap_up(ssid=None):
    iface = ensure_ap_iface()
    if not iface:
        return {"ok": False, "reason": "no wifi hardware for AP mode"}
    ssid = ssid or (load_config().get("HOSTNAME") or gen_hostname())
    os.makedirs(RUN_DIR, exist_ok=True)
    with open(HOSTAPD_CONF, "w") as f:
        f.write("interface=%s\nssid=%s\nhw_mode=g\nchannel=6\n"
                "wpa=0\nignore_broadcast_ssid=0\n" % (iface, ssid))
    with open(DNSMASQ_CONF, "w") as f:
        f.write("interface=%s\nbind-interfaces\n"
                "dhcp-range=192.168.4.10,192.168.4.100,255.255.255.0,12h\n"
                "dhcp-option=3,%s\ndhcp-option=6,%s\naddress=/#/%s\n"
                % (iface, AP_ADDR, AP_ADDR, AP_ADDR))
    run(["ifconfig", iface, "inet", AP_CIDR, "up"])
    run(["hostapd", "-B", HOSTAPD_CONF], timeout=10)
    run(["dnsmasq", "-C", DNSMASQ_CONF], timeout=10)
    return {"ok": True, "reason": "AP up", "ssid": ssid, "ip": AP_ADDR}


def ap_down():
    run(["pkill", "-f", HOSTAPD_CONF], timeout=5)
    run(["pkill", "-f", DNSMASQ_CONF], timeout=5)
    if AP_IF in all_ifaces():
        run(["ifconfig", AP_IF, "destroy"])
    return {"ok": True}


# --------------------------------------------------------------------------
# Chromium kiosk
# --------------------------------------------------------------------------

def kiosk_running():
    rc, _, _ = run(["pgrep", "-f", "kioskage-session"], timeout=5)
    return rc == 0


def kiosk_start(url=None):
    url = url or load_config().get("CONTENT_URL") or DEFAULT_URL
    # Publish the desired URL, then launch just the kiosk (no network re-boot).
    os.makedirs(RUN_DIR, exist_ok=True)
    with open(os.path.join(RUN_DIR, "url"), "w") as f:
        f.write(url + "\n")
    run(["service", "kioskage", "kiosk"], timeout=10)
    return {"ok": True, "url": url}


def kiosk_stop():
    run(["service", "kioskage", "stop"], timeout=10)
    return {"ok": True}


# --------------------------------------------------------------------------
# mDNS / avahi
# --------------------------------------------------------------------------

def ensure_mdns():
    run(["service", "dbus", "onestart"], timeout=10)
    run(["service", "avahi-daemon", "onestart"], timeout=10)


# --------------------------------------------------------------------------
# Aggregate status
# --------------------------------------------------------------------------

def app_version():
    """The deployed git revision recorded by provision/apply.sh, or '' if
    unknown (e.g. a manual install that never ran an OTA update)."""
    try:
        # realpath so this resolves correctly even when kioskagectl is invoked
        # through the /usr/local/sbin/kioskagectl symlink (abspath alone would
        # look for VERSION next to the symlink, not the real install dir).
        p = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                         "..", "VERSION")
        with open(p) as f:
            return f.read().strip()
    except Exception:
        return ""


def tagged_url():
    """The content URL the kiosk should actually load: the saved CONTENT_URL,
    plus this host's name + deployed version appended when the URL is on the
    brand's KEEPALIVE_DOMAIN, so that content server can record per-host
    keep-alive. Tagging only the keep-alive domain means a stick never leaks its
    hostname to arbitrary sites, and the plain URL a customer shares publicly
    carries no host tag (so those views aren't counted). Empty KEEPALIVE_DOMAIN
    disables tagging entirely. Kept in sync with the on-screen landing page,
    which reads this from /api/status."""
    cfg = load_config()
    url = cfg.get("CONTENT_URL") or DEFAULT_URL
    dom = BRAND["KEEPALIVE_DOMAIN"]
    if dom and dom in url:
        host = cfg.get("HOSTNAME") or out(["hostname"]).strip().split(".")[0]
        sep = "&" if "?" in url else "?"
        url = "%s%shost=%s" % (url, sep, host)
        ver = app_version()
        if ver:
            url += "&v=%s" % ver
    return url


def status():
    cfg = load_config()
    eth = ethernet_ifaces()
    eth_status = iface_status(eth[0]) if eth else {"present": False,
                                                   "active": False, "ip": None}
    wifi_present = bool(wlan_devices())
    sta = iface_status(STA_IF) if STA_IF in all_ifaces() else {
        "present": False, "active": False, "ip": None}
    ip = eth_status["ip"] or sta["ip"]
    # Prefer the configured name; otherwise the short (pre-dot) system hostname.
    host = cfg.get("HOSTNAME") or out(["hostname"]).strip().split(".")[0] \
        or gen_hostname()
    return {
        "configured": cfg.get("CONFIGURED") == "yes",
        "setup_mode": in_setup_mode(),
        "setup_ssid": SETUP_SSID,
        "setup_psk": SETUP_PSK,
        # Brand profile (drives the portal/landing UI so no brand text is baked
        # into the static pages). kiosk_key_enabled reflects whether a content
        # base URL is configured for the key field.
        "brand_name": BRAND["BRAND_NAME"],
        "accent_color": BRAND["ACCENT_COLOR"],
        "logo": BRAND["LOGO"],
        "kiosk_key_enabled": bool(KIOSK_URL_BASE),
        "content_url_base": KIOSK_URL_BASE,
        # Portal lock: True once a password is set. auth_mode tells the UI
        # whether to offer a "set password" prompt (local) or not (content).
        "auth_required": auth_configured(),
        "auth_mode": "content" if BRAND["AUTH_URL"] else "local",
        "version": app_version(),
        "hostname": host,
        "mdns": host + ".local" if host else None,
        "content_url": cfg.get("CONTENT_URL", DEFAULT_URL),
        "kiosk_url": tagged_url(),
        "landing_hold": landing_hold(),
        "kiosk_key": cfg.get("KIOSK_KEY", ""),
        "net_mode": cfg.get("NET_MODE"),
        "auto_start": cfg.get("AUTO_START", "yes") == "yes",
        "ethernet": eth_status,
        "wifi": {"present": wifi_present,
                 "active": sta["active"],
                 "ip": sta["ip"],
                 "ssid": current_ssid() or cfg.get("WIFI_SSID")},
        "ip": ip,
        "internet": has_internet() if ip else False,
        "kiosk_running": kiosk_running(),
    }


# --------------------------------------------------------------------------
# High-level provisioning used by the portal
# --------------------------------------------------------------------------

def provision(mode, ssid=None, psk=None, hostname=None, url=None, key=None,
              auto_start=True):
    """Connect, and on success persist config + set hostname + advertise mDNS.
    Returns {ok, reason, ip, hostname, mdns}.

    The content URL comes from an explicit `url` if given, otherwise it is
    built from the kiosk `key` (independent of the hostname — several sticks
    may share one key). Empty both -> the keyless kiosk base."""
    # Refuse an empty Wi-Fi SSID up front (e.g. the scan dropdown never filled)
    # rather than "connecting" to nothing and tearing down a working link.
    if mode == "wifi" and not (ssid or "").strip():
        return {"ok": False, "reason": "no Wi-Fi network selected"}

    # Take exclusive control of the radio: stop the setup-watch loop so it can't
    # re-join the setup hotspot while we switch wlan0 to the chosen network.
    stop_setup_watch()

    if mode == "wifi":
        res = connect_wifi(ssid, psk)
    else:
        res = connect_ethernet()
    if not res["ok"]:
        # Couldn't join the chosen network. Per design, don't silently fall
        # back onto the setup hotspot — return to the on-screen setup ("1st
        # screen") so the user can retry, which also resumes setup-watch.
        enter_setup_mode()
        return {"ok": False, "reason": res["reason"]}

    host = set_hostname(hostname or gen_hostname())
    ensure_mdns()

    key = (key or "").strip()
    content_url = (url or "").strip() or kiosk_url(key)

    cfg = load_config()
    cfg.update({
        "CONFIGURED": "yes",
        "NET_MODE": mode,
        "CONTENT_URL": content_url,
        "KIOSK_KEY": key,
        "HOSTNAME": host,
        "AUTO_START": "yes" if auto_start else "no",
    })
    if mode == "wifi":
        cfg["WIFI_SSID"] = ssid
        cfg["WIFI_PSK"] = psk or ""
    save_config(cfg)

    # Leave setup mode and relaunch the on-screen kiosk. It comes up on the
    # landing screen (address + QR); we set the hold flag so that address stays
    # up long enough for the user to rejoin on the new network — after a
    # hotspot->home switch the configuring phone loses the stick and the TV is
    # the only place the new IP appears. The landing page then advances to
    # content on its own. (No Start click needed — that would require a keyboard
    # on the TV.)
    exit_setup_mode()
    ap_down()
    set_landing_hold()
    kiosk_stop()
    kiosk_start(content_url)

    return {"ok": True, "reason": "connected", "ip": res["ip"],
            "hostname": host, "mdns": host + ".local",
            "internet": has_internet()}


# --------------------------------------------------------------------------
# Setup mode (on-screen onboarding while unconfigured / disconnected)
# --------------------------------------------------------------------------

def primary_ip():
    """Current IPv4 on an ACTIVE ethernet or wifi station (carrier up), or None.
    Requiring an active link means a cable that's been unplugged — whose stale
    DHCP address lingers on the interface — doesn't count as a network."""
    for dev in ethernet_ifaces():
        st = iface_status(dev)
        if st["active"] and st["ip"]:
            return st["ip"]
    if STA_IF in all_ifaces():
        st = iface_status(STA_IF)
        if st["active"] and st["ip"]:
            return st["ip"]
    return None


def release_stale_ip():
    """Drop a lingering DHCP address, route and DNS on any iface whose link is
    down — an unplugged ethernet cable, or a wlan whose network went away (e.g.
    the setup hotspot once we switch to the home network). Left in place, a dead
    link's stale address masquerades as a live network, and — worse — its
    default route and nameserver black-hole all traffic (this is what silently
    breaks OTA/content after a network change). If cleaning removes the default
    route, re-DHCP a still-live iface so routing/DNS come back."""
    devs = ethernet_ifaces() + ([STA_IF] if STA_IF in all_ifaces() else [])
    changed = False
    for dev in devs:
        st = iface_status(dev)
        if st["ip"] and not st["active"]:
            stop_dhclient(dev)
            if default_route_iface() == dev:
                run(["route", "-q", "delete", "default"], timeout=5)
            run(["ifconfig", dev, "inet", st["ip"], "delete"], timeout=5)
            run(["resolvconf", "-d", dev], timeout=5)   # forget its nameserver
            _setup_log("released stale ip on %s (%s)" % (dev, st["ip"]))
            changed = True
    # Reclaim routing on whatever link is still up if we just tore the default
    # route out from under it.
    if changed and not default_route_iface():
        for dev in devs:
            if iface_status(dev)["active"]:
                run(["dhclient", dev], timeout=DHCP_TIMEOUT + 2)
                if default_route_iface():
                    _setup_log("reclaimed default route via %s" % dev)
                    break


def landing_hold():
    return os.path.exists(LANDING_HOLD)


def set_landing_hold():
    os.makedirs(RUN_DIR, exist_ok=True)
    open(LANDING_HOLD, "w").close()


def clear_landing_hold():
    try:
        os.remove(LANDING_HOLD)
    except FileNotFoundError:
        pass


def in_setup_mode():
    return os.path.exists(SETUP_FLAG)


def enter_setup_mode():
    """Flag setup mode, launch the network-watch, and show the portal on HDMI."""
    os.makedirs(RUN_DIR, exist_ok=True)
    open(SETUP_FLAG, "w").close()
    start_setup_watch()
    kiosk_start()


def exit_setup_mode():
    try:
        os.remove(SETUP_FLAG)
    except FileNotFoundError:
        pass


def factory_reset():
    """Forget everything and return to a clean setup: remove the saved config
    and setup flag, and reset the hostname to the mac-derived default so no
    prior identity (WiFi, kiosk key, name) survives."""
    for p in (CONF_PATH, SETUP_FLAG, LANDING_HOLD):
        try:
            os.remove(p)
        except FileNotFoundError:
            pass
    clear_creds()   # a wiped stick re-opens (physical reset = the auth bypass)
    host = set_hostname(gen_hostname())
    return {"ok": True, "hostname": host}


_setup_tried = set()
SETUP_LOG = os.path.join(RUN_DIR, "setup.log")


def _setup_log(msg):
    try:
        os.makedirs(RUN_DIR, exist_ok=True)
        with open(SETUP_LOG, "a") as f:
            f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass


def try_setup_hotspot():
    """Join the setup hotspot: any network that accepts SETUP_PSK. iPhone
    hotspots are named after the device, so we match by PASSWORD, not SSID —
    trying the strongest secured networks first (the phone is in the room). A
    network literally named SETUP_SSID is tried first as an Android fast path.
    Failed networks are remembered so neighbours aren't re-hammered each cycle."""
    try:
        nets = scan_wifi()          # strongest signal first
    except Exception as e:
        _setup_log("scan failed: %s" % e)
        return False
    _setup_log("scan sees: " + (", ".join(
        "%s(%s%s)" % (n["ssid"], n["signal"], "*" if n.get("secure") else "")
        for n in nets[:12]) or "(nothing)"))
    ordered = ([n for n in nets if n["ssid"] == SETUP_SSID] +
               [n for n in nets if n["ssid"] != SETUP_SSID])
    tries = 0
    for n in ordered:
        ssid = n["ssid"]
        if not ssid or not n.get("secure") or ssid in _setup_tried:
            continue
        _setup_tried.add(ssid)
        _setup_log("trying '%s' with setup password ..." % ssid)
        r = connect_wifi(ssid, SETUP_PSK, assoc_timeout=SETUP_ASSOC)
        if r["ok"]:
            _setup_log("JOINED '%s' ip=%s" % (ssid, r.get("ip")))
            return True
        _setup_log("  '%s' failed: %s" % (ssid, r.get("reason")))
        tries += 1
        if tries >= SETUP_MAX_TRY:
            break
    return False


def setup_watch():
    """Background loop while unconfigured: bring up whatever network the user
    provides — an Ethernet cable the moment it's plugged in, or a phone hotspot
    with the setup password — so the on-screen portal becomes reachable. Exits
    once the stick is configured."""
    _setup_log("setup-watch started")
    cycles = 0
    # Tie our lifetime to setup mode: once provision() leaves setup mode (or the
    # stick is configured) we must stop touching the radio, so a provision in
    # flight isn't fought for wlan0.
    while load_config().get("CONFIGURED") != "yes" and in_setup_mode():
        if not primary_ip():
            release_stale_ip()                       # drop a dead link's stale IP
            connect_ethernet()                       # cable, the moment it's live
            # Re-check just before we hijack the radio: a provision() may have
            # started (and left setup mode) while we were connecting/scanning.
            if not primary_ip() and in_setup_mode():
                if cycles % SETUP_RETRY_CYCLES == 0 and cycles:
                    _setup_tried.clear()             # periodically re-try everything
                    _setup_log("re-trying all networks")
                try_setup_hotspot()
            if primary_ip():
                _setup_log("network up: %s" % primary_ip())
                _setup_tried.clear()
                ensure_mdns()
        cycles += 1
        time.sleep(SETUP_POLL)
    _setup_log("configured — setup-watch exiting")


def start_setup_watch():
    """Launch setup_watch() as a detached background process (idempotent)."""
    if run(["pgrep", "-f", "kioskagectl.*setup-watch"], timeout=5)[0] == 0:
        return
    run(["daemon", "-f", sys.executable, os.path.abspath(__file__),
         "setup-watch"], timeout=10)


def stop_setup_watch():
    """Kill the background setup-watch loop. Called before provision() takes
    over the radio so the watch can't re-join the setup hotspot mid-switch and
    fight us for the single wlan interface (which otherwise strands the stick
    back on the hotspot instead of the network the user just chose)."""
    run(["pkill", "-f", "kioskagectl.*setup-watch"], timeout=5)
    time.sleep(0.5)  # let it die before we touch wlan0


# --------------------------------------------------------------------------
# Boot state machine (called by rc.d/kioskage at startup)
# --------------------------------------------------------------------------

def boot():
    cfg = load_config()
    ensure_mdns()
    # A fresh boot is not a provision hand-off: show the address only briefly.
    clear_landing_hold()
    # Heal any stale address/route/DNS left by an interface that went away while
    # powered off (e.g. moved from one network to another between boots).
    release_stale_ip()
    if cfg.get("CONFIGURED") != "yes":
        # Unconfigured: bring up a live cable immediately, then enter setup mode
        # (network-watch + on-screen portal) so it can be onboarded.
        connect_ethernet()
        enter_setup_mode()
        return {"provisioned": False, "setup": True}

    mode = cfg.get("NET_MODE", "ethernet")
    if mode == "wifi":
        res = connect_wifi(cfg.get("WIFI_SSID"), cfg.get("WIFI_PSK"))
    else:
        res = connect_ethernet()

    if not res["ok"]:
        # Saved network unreachable (e.g. the WiFi changed): drop to setup mode
        # so it can be re-provisioned on screen.
        enter_setup_mode()
        return {"provisioned": True, "connected": False,
                "reason": res["reason"], "setup": True}

    exit_setup_mode()
    set_hostname(cfg.get("HOSTNAME") or gen_hostname())
    ensure_mdns()
    # Content-validated mode: adopt/refresh this key's credential hashes so the
    # stick locks itself and can authenticate offline (no-op in local mode).
    sync_creds()
    if cfg.get("AUTO_START", "yes") == "yes":
        kiosk_start(cfg.get("CONTENT_URL"))
    return {"provisioned": True, "connected": True, "ip": res["ip"]}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _print(obj):
    print(json.dumps(obj, indent=2))


def main(argv):
    if not argv:
        _print(status())
        return 0
    cmd = argv[0]
    if cmd == "status":
        _print(status())
    elif cmd == "scan":
        _print(scan_wifi())
    elif cmd == "hostname-gen":
        print(gen_hostname())
    elif cmd == "hostname-set":
        print(set_hostname(argv[1]))
    elif cmd == "connect":
        # connect wifi <ssid> <psk> | connect ethernet
        if argv[1] == "wifi":
            _print(connect_wifi(argv[2], argv[3] if len(argv) > 3 else ""))
        else:
            _print(connect_ethernet())
    elif cmd == "ap-up":
        _print(ap_up())
    elif cmd == "ap-down":
        _print(ap_down())
    elif cmd == "kiosk-start":
        _print(kiosk_start(argv[2] if len(argv) > 2 else None))
    elif cmd == "kiosk-stop":
        _print(kiosk_stop())
    elif cmd == "setup-watch":
        setup_watch()
    elif cmd == "reset":
        _print(factory_reset())
    elif cmd == "boot":
        _print(boot())
    else:
        sys.stderr.write("unknown command: %s\n" % cmd)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
