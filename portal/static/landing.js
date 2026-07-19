// On-screen landing screen for the kiosk TV.
//
// Shown on every kiosk launch. It displays this stick's web address, IP and a
// QR (which encodes http://<ip>/), then advances to the configured content.
//
// Two paces, driven by the server's `landing_hold` flag:
//   - normal boot            -> brief splash, then content
//   - just after a provision -> hold the address/QR much longer, because a
//     hotspot->home-network switch drops the configuring phone and this screen
//     is the only place the new IP shows up. The user rejoins via the QR.
(function () {
  var HOLD_SECS = 45;   // after a provision hand-off
  var BOOT_SECS = 8;    // ordinary boot flash

  var addr = document.getElementById("addr");
  var ipEl = document.getElementById("ip");
  var qr = document.getElementById("qr");
  var lead = document.getElementById("lead");
  var msg = document.getElementById("msg");
  var count = document.getElementById("count");

  var target = null;      // content URL to advance to (server-tagged)
  var hold = false;
  var remaining = null;   // countdown; stays null until we have net + a target
  var qrSeq = 0;
  var lastIp = null;

  function refreshQr() {
    // Cache-bust so the QR re-renders for the new IP after a network switch.
    qr.src = "/qr.svg?t=" + (++qrSeq);
  }

  function apply(s) {
    hold = !!s.landing_hold;
    addr.textContent = s.mdns || (s.hostname ? s.hostname + ".local" : "—");
    ipEl.textContent = s.ip || "waiting for network…";
    target = s.kiosk_url || s.content_url || null;

    if (s.ip && s.ip !== lastIp) { lastIp = s.ip; refreshQr(); }

    if (hold) {
      lead.textContent = "Setup complete — this display is now on your network.";
      msg.textContent = "Your content starts automatically. Scan the code only if you need to change settings later.";
    } else {
      lead.textContent = "This display is online.";
      msg.textContent = "";
    }

    // Only begin the countdown once there is actually a network and a target;
    // an offline stick keeps showing its address rather than a browser error.
    if (remaining === null && s.ip && target) {
      remaining = hold ? HOLD_SECS : BOOT_SECS;
    }
  }

  function poll() {
    fetch("/api/status", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(apply)
      .catch(function () { /* portal momentarily down; try again next tick */ });
  }

  function advance() {
    if (!target) return;
    if (hold) {
      // Best-effort: drop the hold so a later reboot shows only the brief flash.
      fetch("/api/landing/done", { method: "POST" }).catch(function () {});
    }
    window.location.replace(target);
  }

  function tick() {
    if (remaining === null) { count.textContent = ""; return; }
    if (remaining <= 0) { advance(); return; }
    count.textContent = "Showing content in " + remaining + "s";
    remaining--;
  }

  poll();
  setInterval(poll, 5000);
  setInterval(tick, 1000);
})();
