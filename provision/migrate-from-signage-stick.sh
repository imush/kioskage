#!/bin/sh
#
# migrate-from-signage-stick.sh — move a stick from the legacy `signage-stick`
# install to kioskage (public code + optional private brand overlay).
#
# WHERE THIS LIVES / RUNS: the migration LOGIC lives here in kioskage. A stick
# still running signage-stick is nudged onto it by ONE handoff commit pushed to
# the (otherwise frozen) signage-stick repo, whose apply.sh clones kioskage and
# runs this script — see docs/MIGRATION.md. So only a tiny trigger touches the
# old repo, and only when you deliberately execute the migration.
#
# >>> TEST ON A THROWAWAY STICK FIRST. <<< It switches service names, repoints
# OTA and reboots a live system; a bad migration on a NAT'd stick can't be fixed
# remotely.
#
# FAIL-SAFE DESIGN. Nothing is irreversible until kioskage is proven to drive
# BOTH the portal (:80) AND the actual display (Xorg + Chromium). Until that
# point an EXIT trap fully restores the signage stack (screen + portal) on any
# error, so a failed migration always leaves the stick alive on signage. The
# legacy services/OTA are retired only AFTER kioskage is confirmed healthy, and
# the "migration done" marker (which the trigger guards on) is written at that
# same instant — so a migration that fails partway can simply be retried by
# re-pushing the trigger, and never strands the stick in a half-migrated state.
#
# Usage:  sh migrate-from-signage-stick.sh [overlay-repo-url] [channel]
#   overlay-repo-url  optional PRIVATE brand overlay (git@github.com:you/x.git).
#                     Register the stick's existing deploy key on THAT repo first.
#   channel           release channel this stick follows: prod (fleet, default)
#                     or staging (canary). Written to kioskage-overlay.conf.
#
set -eu

REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)          # the kioskage checkout
PREFIX=/usr/local
OVERLAY_REPO=${1:-${KIOSKAGE_OVERLAY_REPO:-}}
CHANNEL=${2:-${KIOSKAGE_BRANCH:-prod}}
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-30}   # seconds to wait for the portal on :80
KIOSK_TIMEOUT=${KIOSK_TIMEOUT:-90}     # seconds to wait for Xorg + Chromium
MARKER="$PREFIX/etc/kioskage-migrated" # written at the point of no return

log() { echo ">>> $*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

# ── Health probes ──────────────────────────────────────────────────────────
portal_ok() {
  i=0
  while [ "$i" -lt "$HEALTH_TIMEOUT" ]; do
    fetch -qo /dev/null "http://127.0.0.1/api/status" 2>/dev/null && return 0
    sleep 1; i=$((i + 1))
  done
  return 1
}

kiosk_ok() {
  i=0
  while [ "$i" -lt "$KIOSK_TIMEOUT" ]; do
    pgrep -q Xorg && pgrep -q chrome && return 0
    sleep 1; i=$((i + 1))
  done
  return 1
}

# ── Fail-safe restore ──────────────────────────────────────────────────────
# Was a display actually running before we touched anything? Only then do we
# demand kioskage bring one back (and only then restore signage's on abort).
kiosk_before=no
pgrep -q chrome 2>/dev/null && kiosk_before=yes

COMMITTED=no   # flips to yes at the point of no return (signage retired)

restore_signage() {
  log "Restoring the signage stack (portal + display)"
  service kioskage stop        >/dev/null 2>&1 || true   # kioskage kiosk/display
  service kioskage_portal stop >/dev/null 2>&1 || true
  service signage_portal start >/dev/null 2>&1 || true
  [ "$kiosk_before" = yes ] && service signage start >/dev/null 2>&1 || true
}

on_exit() {
  rc=$?
  if [ "$rc" -ne 0 ] && [ "$COMMITTED" != yes ]; then
    log "!! Migration aborted (rc=$rc) before cutover completed"
    restore_signage
    log "Stick left running on signage. Re-push the trigger to retry."
  fi
}
trap on_exit EXIT

log "Migrating signage-stick -> kioskage (from $REPO_DIR)"

# 1. Carry over the saved config (identical KEY="value" format, new path).
if [ -f "$PREFIX/etc/signage.conf" ] && [ ! -f "$PREFIX/etc/kioskage.conf" ]; then
  log "Migrating config signage.conf -> kioskage.conf"
  cp "$PREFIX/etc/signage.conf" "$PREFIX/etc/kioskage.conf"
  chmod 600 "$PREFIX/etc/kioskage.conf"
fi

# 2. Record the release channel + private brand overlay in the deploy config.
log "Setting release channel: $CHANNEL${OVERLAY_REPO:+, overlay: $OVERLAY_REPO}"
{
  printf 'KIOSKAGE_BRANCH="%s"\n' "$CHANNEL"
  [ -n "$OVERLAY_REPO" ] && printf 'KIOSKAGE_OVERLAY_REPO="%s"\n' "$OVERLAY_REPO"
} > "$PREFIX/etc/kioskage-overlay.conf"

# 3. Install kioskage (creates the kioskage user, files, neutral brand.conf,
#    rc.d services, enables them in rc.conf, installs the OTA updater + cron).
#    Package installs are a fast no-op on a stick that already has them.
#    Signage is still running throughout — a failure here trips the trap and
#    restores it untouched.
log "Running kioskage install.sh"
sh "$REPO_DIR/provision/install.sh"

# 4. Pull + apply the overlay now so the very first boot is already branded,
#    reusing the OTA path (clone code+overlay, apply, health-check, rollback).
if [ -n "$OVERLAY_REPO" ]; then
  log "Applying brand overlay via kioskage-update"
  KIOSKAGE_SRC="$REPO_DIR" "$PREFIX/sbin/kioskage-update" \
    || log "overlay apply reported an error (will retry on the next cron run)"
fi

# 5. Cut over to kioskage and PROVE it healthy — portal AND, if a display was
#    running, the real screen (Xorg + Chromium) — before retiring anything.
#    Both portals bind :80, so signage must stop first. Any failure below trips
#    the trap and restores signage.
log "Cutting over to kioskage (portal + display)"
service signage stop        >/dev/null 2>&1 || true
service signage_portal stop >/dev/null 2>&1 || true
service dbus start          >/dev/null 2>&1 || true
service avahi_daemon start  >/dev/null 2>&1 || true
service kioskage_portal start >/dev/null 2>&1 || true

if ! portal_ok; then
  log "!! kioskage portal did not answer on :80"
  exit 1
fi
log "kioskage portal healthy on :80"

if [ "$kiosk_before" = yes ]; then
  log "Starting + verifying the kioskage display"
  # kioskagectl publishes the saved CONTENT_URL and launches the kiosk service.
  "$PREFIX/sbin/kioskagectl" kiosk-start >/dev/null 2>&1 || true
  if ! kiosk_ok; then
    log "!! kioskage display (Xorg + Chromium) did not come up — refusing to"
    log "   retire signage in favour of a dark screen"
    exit 1
  fi
  log "kioskage display healthy (Xorg + Chromium)"
fi

# 6. POINT OF NO RETURN: kioskage is proven (portal + screen). Mark migration
#    done (the trigger guards on this file) and retire the legacy stack. From
#    here the trap no longer restores signage.
COMMITTED=yes
touch "$MARKER"
log "Retiring legacy signage services + cron"
sysrc signage_enable=NO signage_portal_enable=NO >/dev/null 2>&1 || true
crontab -l 2>/dev/null | grep -v 'signage-update' | crontab - 2>/dev/null || true

# 7. Reboot into a clean kioskage boot. We already proved these exact services
#    come up in this environment, so the clean boot is low-risk; signage files
#    remain installed (only disabled) as a last-resort manual recovery path.
log "Migration complete. Rebooting into kioskage in 5s…"
sync
( sleep 5; reboot ) &
