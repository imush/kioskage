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
# Safety: kioskage is installed and health-checked with the legacy install
# still intact; the legacy services/OTA are only retired AFTER the kioskage
# portal answers on :80. If the check fails, signage is restored and we abort.
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
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-30}

log() { echo ">>> $*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

portal_ok() {
  i=0
  while [ "$i" -lt "$HEALTH_TIMEOUT" ]; do
    fetch -qo /dev/null "http://127.0.0.1/api/status" 2>/dev/null && return 0
    sleep 1; i=$((i + 1))
  done
  return 1
}

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
log "Running kioskage install.sh"
sh "$REPO_DIR/provision/install.sh"

# 4. Pull + apply the overlay now so the very first boot is already branded,
#    reusing the OTA path (clone code+overlay, apply, health-check, rollback).
if [ -n "$OVERLAY_REPO" ]; then
  log "Applying brand overlay via kioskage-update"
  KIOSKAGE_SRC="$REPO_DIR" "$PREFIX/sbin/kioskage-update" \
    || log "overlay apply reported an error (will retry on the next cron run)"
fi

# 5. Cut over :80 from signage to kioskage and health-check BEFORE retiring the
#    legacy install (both portals bind :80, so signage must stop first).
log "Cutting over the portal to kioskage"
service signage stop        >/dev/null 2>&1 || true
service signage_portal stop >/dev/null 2>&1 || true
service dbus start          >/dev/null 2>&1 || true
service avahi_daemon start  >/dev/null 2>&1 || true
service kioskage_portal start >/dev/null 2>&1 || true

if ! portal_ok; then
  log "!! kioskage portal did not come up — restoring signage and aborting"
  service kioskage_portal stop >/dev/null 2>&1 || true
  service signage_portal start  >/dev/null 2>&1 || true
  exit 1
fi
log "kioskage portal healthy on :80"

# 6. Point of no return: retire the legacy signage services + OTA cron.
log "Retiring legacy signage services + cron"
sysrc signage_enable=NO signage_portal_enable=NO >/dev/null 2>&1 || true
crontab -l 2>/dev/null | grep -v 'signage-update' | crontab - 2>/dev/null || true

# 7. Reboot into kioskage (clean boot runs kioskage's boot state machine).
log "Migration complete. Rebooting into kioskage in 5s…"
sync
( sleep 5; reboot ) &
