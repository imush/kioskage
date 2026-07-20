"use strict";

const $ = (id) => document.getElementById(id);
const api = {
  get: (p) => fetch(p).then((r) => r.json()),
  post: (p, b) => fetch(p, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(b || {}),
  }).then((r) => r.json()),
};

let mode = "ethernet";
let pollTimer = null;
let formRevealed = false;
let contentBase = "";   // brand CONTENT_URL_BASE, filled from /api/status

// "Keyboard" setup link: reveal the config form even before a network exists.
document.addEventListener("DOMContentLoaded", () => {
  const rf = document.getElementById("revealForm");
  if (rf) rf.addEventListener("click", (e) => {
    e.preventDefault();
    formRevealed = true;
    document.getElementById("configForm").classList.remove("hidden");
  });
});

// ---- Network mode toggle -------------------------------------------------
function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function setMode(m) {
  mode = m;
  document.querySelectorAll("#modeSeg button")
    .forEach((b) => b.classList.toggle("active", b.dataset.mode === m));
  $("ethPane").classList.toggle("hidden", m !== "ethernet");
  $("wifiPane").classList.toggle("hidden", m !== "wifi");
  if (m === "wifi" && $("ssid").options.length <= 1) scan();
}

$("modeSeg").addEventListener("click", (e) => {
  const btn = e.target.closest("button");
  if (!btn || btn.disabled) return;
  setMode(btn.dataset.mode);
});

$("showPsk").addEventListener("change", (e) => {
  $("psk").type = e.target.checked ? "text" : "password";
});

$("hostname").addEventListener("input", () => {
  $("mdnsPreview").textContent = $("hostname").value || "kiosk-xxxxxx";
});

// The kiosk key builds the content URL (independent of the device name).
$("kioskKey").addEventListener("input", () => {
  const k = $("kioskKey").value.trim();
  if (k && contentBase) $("url").value = contentBase + "?key=" + encodeURIComponent(k);
});

$("rescan").addEventListener("click", scan);

function scan() {
  const sel = $("ssid");
  sel.innerHTML = '<option value="">Scanning&hellip;</option>';
  api.get("/api/scan").then((d) => {
    const nets = (d.networks || []);
    if (!nets.length) {
      sel.innerHTML = '<option value="">No networks found</option>';
      return;
    }
    sel.innerHTML = '<option value="">Select a network&hellip;</option>';
    nets.forEach((n) => {
      const o = document.createElement("option");
      o.value = n.ssid;
      const lock = n.secure ? " 🔒" : "";
      const sig = n.signal != null ? ` (${n.signal} dBm)` : "";
      o.textContent = n.ssid + lock + sig;
      sel.appendChild(o);
    });
  }).catch(() => {
    sel.innerHTML = '<option value="">Scan failed</option>';
  });
}

// ---- On-screen setup panel ----------------------------------------------
function updateSetup(s) {
  const showSetup = s.setup_mode || !s.configured;
  $("setupPanel").classList.toggle("hidden", !showSetup);
  if (!showSetup) return;
  $("setupPsk").textContent = s.setup_psk || "hckioskage";
  const hasNet = !!s.ip;
  $("setupConnect").classList.toggle("hidden", hasNet);   // hide "how to connect" once online
  $("setupReady").classList.toggle("hidden", !hasNet);
  $("setupTitle").textContent = hasNet ? "Finish setup" : "Set up this display";
  // Before any network, show ONLY the instructions — hide the form until we're
  // online (or the user reveals it for keyboard setup).
  $("configForm").classList.toggle("hidden", !hasNet && !formRevealed);
  if (hasNet) {
    const addr = "http://" + s.ip + "/";
    if ($("setupAddr").textContent !== addr) {   // refresh QR only on IP change
      $("setupAddr").textContent = addr;
      $("setupQr").src = "/qr.svg?ip=" + encodeURIComponent(s.ip);
    }
  }
}

// Live status bits that must refresh on every poll — device line, ethernet
// hint, connection banner and the setup panel (separate from the one-time
// form prefills in refreshStatus).
function updateStatus(s) {
  updateSetup(s);
  $("deviceLine").textContent = `${s.hostname} · ${s.ip || "no IP yet"}`;
  const eth = s.ethernet || {};
  $("ethStatus").textContent = eth.active
    ? `Cable connected${eth.ip ? " · " + eth.ip : ""}`
    : "No cable detected — plug in Ethernet or use Wi-Fi";
  // Only report "Connected" with a real IP (association alone — e.g. a failing
  // WPA handshake — is not connected); setup panel owns the display in setup.
  const banner = $("connBanner");
  const wifi = s.wifi || {};
  if (s.setup_mode) {
    banner.classList.add("hidden");
  } else if (wifi.ip && wifi.ssid) {
    banner.innerHTML = `Connected to <span class="net">${escapeHtml(wifi.ssid)}</span> ` +
      `<span class="addr">Wi-Fi · ${wifi.ip}</span>`;
    banner.classList.remove("hidden");
  } else if (eth.ip) {
    banner.innerHTML = `Connected via <span class="net">Ethernet</span> ` +
      `<span class="addr">· ${eth.ip}</span>`;
    banner.classList.remove("hidden");
  } else {
    banner.classList.add("hidden");
  }
  updateAuth(s);
}

// ---- Portal lock (login gate + set-password affordance) ------------------
function updateAuth(s) {
  const locked = s.auth_required && !s.authed;
  $("authPanel").classList.toggle("hidden", !locked);
  if (locked) {
    // A password is set and this browser isn't logged in: gate everything.
    $("configForm").classList.add("hidden");
    const sp = $("setupPanel"); if (sp) sp.classList.add("hidden");
    $("securityRow").classList.add("hidden");
    return;
  }
  // Unlocked (open stick) or logged in: offer the set/change-password row when
  // the config form is on screen. In content-auth mode the password lives on
  // the content server, so hide the local set-password affordance.
  // Unlocked. For a configured stick, updateSetup() returns early and won't
  // manage the config form, so re-show it here (it was hidden while locked) —
  // otherwise the page looks blank right after a successful login.
  if (s.configured && !s.setup_mode) $("configForm").classList.remove("hidden");
  const configVisible = !$("configForm").classList.contains("hidden");
  $("securityRow").classList.toggle("hidden", !configVisible);
  $("logoutLink").classList.toggle("hidden", !s.authed);
  $("lockToggle").classList.toggle("hidden", s.auth_mode === "content");
}

$("authLogin").addEventListener("click", () => {
  api.post("/api/auth/login", { password: $("authPw").value }).then((r) => {
    if (r.ok) { $("authPw").value = ""; $("authErr").classList.add("hidden"); refreshStatus(); }
    else { $("authErr").classList.remove("hidden"); }
  });
});
$("authPw").addEventListener("keydown", (e) => { if (e.key === "Enter") $("authLogin").click(); });
$("lockToggle").addEventListener("click", (e) => {
  e.preventDefault(); $("lockForm").classList.toggle("hidden");
});
$("setPw").addEventListener("click", () => {
  api.post("/api/auth/set-password", { password: $("newPw").value }).then((r) => {
    $("newPw").value = "";
    $("pwMsg").textContent = !r.ok ? "Failed."
      : (r.auth_required ? "Password set — display locked." : "Lock removed.");
    refreshStatus();
  });
});
$("logoutLink").addEventListener("click", (e) => {
  e.preventDefault();
  api.post("/api/auth/logout", {}).then(() => refreshStatus());
});

// ---- Brand (from /api/status; no brand text is baked into the page) ------
function applyBrand(s) {
  contentBase = s.content_url_base || "";
  const name = s.brand_name || "Kioskage";
  document.title = name + " Setup";
  const t = $("brandTitle"); if (t) t.textContent = name + " Setup";
  const lg = $("brandLogo"); if (lg) lg.alt = name;
  if (s.accent_color)
    document.documentElement.style.setProperty("--accent", s.accent_color);
  // Hide the kiosk-key field when there's no content base to build a URL from.
  const kk = $("kioskKey"); if (kk) kk.style.display = s.kiosk_key_enabled ? "" : "none";
  // Content-validated mode needs the key's password (the "claim") to switch keys.
  const kkp = $("kioskKeyPw");
  if (kkp) kkp.classList.toggle("hidden", !(s.kiosk_key_enabled && s.auth_mode === "content"));
}

// ---- Initial status ------------------------------------------------------
function refreshStatus() {
  api.get("/api/status").then((s) => {
    applyBrand(s);
    updateStatus(s);
    if (!$("hostname").value) {
      $("hostname").value = s.hostname || "";
      $("mdnsPreview").textContent = s.hostname || "kiosk-xxxxxx";
    }
    if (!$("url").value) $("url").value = s.content_url || "";
    if (!$("kioskKey").value) $("kioskKey").value = s.kiosk_key || "";
    $("autostart").checked = s.auto_start !== false;

    // Pre-fill the network the stick is already on (or configured for) so a
    // configured stick's portal doesn't ask you to pick it again. Adding it as
    // a selected option also stops setMode() from auto-scanning (which would be
    // destructive on the single radio while it's holding this connection).
    const knownSsid = (s.wifi && s.wifi.ssid) || "";
    if (knownSsid) {
      const sel = $("ssid");
      if (![...sel.options].some((o) => o.value === knownSsid)) {
        const o = document.createElement("option");
        o.value = knownSsid;
        o.textContent = knownSsid + " (connected)";
        o.selected = true;
        sel.insertBefore(o, sel.firstChild);
      }
    }

    if (!(s.wifi && s.wifi.present)) {
      // No wifi hardware: force ethernet
      document.querySelector('[data-mode="wifi"]').disabled = true;
    } else if (s.net_mode) {
      // Pre-select the tab matching how the stick is configured
      setMode(s.net_mode);
    }
    if (s.kiosk_running) showResult(s, true, true);
  }).catch(() => {});
}

// ---- Connect -------------------------------------------------------------
$("connect").addEventListener("click", () => {
  const params = {
    mode,
    ssid: $("ssidManual").value || $("ssid").value,
    psk: $("psk").value,
    hostname: $("hostname").value,
    url: $("url").value,
    key: $("kioskKey").value.trim(),
    key_password: $("kioskKeyPw").value,
    auto_start: $("autostart").checked,
  };
  if (mode === "wifi" && !params.ssid) {
    alert("Pick or type a Wi-Fi network first.");
    return;
  }
  $("connect").disabled = true;
  $("result").classList.add("hidden");
  $("progress").classList.remove("hidden");
  $("progressMsg").textContent =
    mode === "wifi" ? "Joining Wi-Fi…" : "Bringing up Ethernet…";

  api.post("/api/connect", params).then((r) => {
    if (r.state !== "running") {
      finish({ ok: false, reason: r.reason || "could not start" });
      return;
    }
    pollTimer = setInterval(pollConnect, 1500);
  });
});

function pollConnect() {
  api.get("/api/connect/status").then((a) => {
    if (a.state === "done") {
      clearInterval(pollTimer);
      finish(a.result || { ok: false, reason: "no result" });
    }
  }).catch(() => {
    // Poll failed: likely the phone dropped off the AP after success.
    $("progressMsg").textContent =
      "Lost connection to the stick. If you were on its Wi-Fi, rejoin your " +
      "own network — setup may have succeeded.";
  });
}

function finish(res) {
  $("progress").classList.add("hidden");
  $("connect").disabled = false;
  if (res.ok) {
    showResult(res, true, false);
  } else {
    const p = $("result");
    p.className = "panel err";
    p.classList.remove("hidden");
    $("resultMsg").textContent = "❌ " + (res.reason || "Connection failed");
    $("resultDetail").classList.add("hidden");
    $("startRow").classList.add("hidden");
  }
}

function showResult(res, ok, running) {
  const p = $("result");
  p.className = "panel ok";
  p.classList.remove("hidden");
  $("resultMsg").textContent = running
    ? "✅ Running"
    : "✅ Connected";
  if (res.ip || res.mdns) {
    $("ipOut").textContent = res.ip || (res.ethernet && res.ethernet.ip) || "—";
    $("mdnsOut").textContent = res.mdns || (res.hostname && res.hostname + ".local") || "—";
    $("resultDetail").classList.remove("hidden");
  }
  $("startRow").classList.remove("hidden");
  $("stopKiosk").classList.toggle("hidden", !running);
  $("startKiosk").textContent = running ? "Restart" : "Start";
}

$("startKiosk").addEventListener("click", () => {
  $("startKiosk").disabled = true;
  api.post("/api/kiosk/restart", { url: $("url").value }).then(() => {
    $("startKiosk").disabled = false;
    $("startKiosk").textContent = "Restart";
    $("stopKiosk").classList.remove("hidden");
    $("resultMsg").textContent = "✅ Running";
  });
});

$("stopKiosk").addEventListener("click", () => {
  api.post("/api/kiosk/stop", {}).then(() => {
    $("stopKiosk").classList.add("hidden");
    $("startKiosk").textContent = "Start";
    $("resultMsg").textContent = "✅ Connected (kioskage stopped)";
  });
});

refreshStatus();
// Keep the on-screen setup panel live as a network appears (the TV has no one
// to reload it), and switch it off once the stick is configured.
setInterval(() => api.get("/api/status").then(updateStatus).catch(() => {}), 5000);
