#!/bin/sh
#
# apply.sh - install the current repo's application files onto an already-
# provisioned stick and restart the affected services. This is the fast path
# used by OTA app updates (kioskage-update): it copies the portal, rc.d and X
# session files and bounces the portal, but does NOT touch packages, the user,
# or rc.conf (that is provision/install.sh's one-time job).
#
# Usage: sh provision/apply.sh [--kiosk]
#   --kiosk  also restart the running Chromium kiosk (needed when the X session
#            files changed; omit for portal-only updates to avoid a screen blink)
#
set -eu

REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)
PREFIX=/usr/local
KIOSKAGE_HOME=/usr/local/libexec/kioskage
KIOSKAGE_USER=kioskage

log() { echo ">>> $*"; }

[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

log "Applying app files from $REPO_DIR"
mkdir -p "$KIOSKAGE_HOME"

# apply.sh normally avoids package changes, but the xinitrc needs unclutter
# (hides the idle mouse cursor); ensure it's present so the fix reaches already-
# deployed sticks via OTA.
pkg info -e unclutter >/dev/null 2>&1 || pkg install -y unclutter >/dev/null 2>&1 || true

# Replace the portal wholesale so files removed upstream don't linger.
rm -rf "$KIOSKAGE_HOME/portal"
cp -R "$REPO_DIR/portal" "$KIOSKAGE_HOME/portal"
cp "$REPO_DIR/etc/xinitrc"       "$KIOSKAGE_HOME/xinitrc"
cp "$REPO_DIR/etc/kioskage-session" "$KIOSKAGE_HOME/kioskage-session"
chmod +x "$KIOSKAGE_HOME/kioskage-session" "$KIOSKAGE_HOME/portal/kioskagectl.py"
cp "$KIOSKAGE_HOME/xinitrc" "/home/$KIOSKAGE_USER/.xinitrc" 2>/dev/null || true
chown -R "$KIOSKAGE_USER:$KIOSKAGE_USER" "/home/$KIOSKAGE_USER" 2>/dev/null || true
ln -sf "$KIOSKAGE_HOME/portal/kioskagectl.py" "$PREFIX/sbin/kioskagectl"

# Keep the OTA updater itself current so future updates use the latest logic
# (rollback, health checks, ...).
install -m 755 "$REPO_DIR/bin/kioskage-update" "$PREFIX/sbin/kioskage-update"

# rc.d service scripts (in case they changed upstream).
cp "$REPO_DIR/rc.d/kioskage"         "$PREFIX/etc/rc.d/kioskage"
cp "$REPO_DIR/rc.d/kioskage_portal"  "$PREFIX/etc/rc.d/kioskage_portal"
chmod +x "$PREFIX/etc/rc.d/kioskage" "$PREFIX/etc/rc.d/kioskage_portal"

# --- Brand: kioskage's neutral default, then the private overlay on top ------
# The appliance reads /usr/local/etc/kioskage-brand.conf. Start from the neutral
# default shipped in the (public) code repo, then, if a brand overlay was pulled
# (KIOSKAGE_OVERLAY_SRC, set by kioskage-update), copy its files over — the
# brand.conf, an optional logo, and any static portal overrides it ships.
cp "$REPO_DIR/etc/brand.conf" "$PREFIX/etc/kioskage-brand.conf"
OVERLAY="${KIOSKAGE_OVERLAY_SRC:-}"
if [ -n "$OVERLAY" ] && [ -d "$OVERLAY" ]; then
  log "Applying brand overlay from $OVERLAY"
  [ -f "$OVERLAY/brand.conf" ] && cp "$OVERLAY/brand.conf" "$PREFIX/etc/kioskage-brand.conf"
  [ -f "$OVERLAY/logo.png" ]   && cp "$OVERLAY/logo.png"   "$KIOSKAGE_HOME/portal/static/logo.png"
  [ -d "$OVERLAY/portal/static" ] && cp -R "$OVERLAY/portal/static/." "$KIOSKAGE_HOME/portal/static/"
fi

# Record the deployed revision so the portal (and fleet tooling) can report it.
# Include the overlay's short rev when present so a stick's exact state is known.
ver=$(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)
if [ -n "$OVERLAY" ] && [ -d "$OVERLAY/.git" ]; then
  ver="$ver+$(git -C "$OVERLAY" rev-parse --short HEAD 2>/dev/null || echo unknown)"
fi
printf '%s\n' "$ver" > "$KIOSKAGE_HOME/VERSION"

log "Restarting the configuration portal"
service kioskage_portal restart >/dev/null 2>&1 \
  || service kioskage_portal start  >/dev/null 2>&1 || true

if [ "${1:-}" = "--kiosk" ]; then
  log "Restarting the kiosk display"
  service kioskage stop  >/dev/null 2>&1 || true
  service kioskage kiosk >/dev/null 2>&1 || true
fi

log "Applied revision $(git -C "$REPO_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
