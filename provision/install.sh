#!/bin/sh
#
# install.sh - provision a fresh FreeBSD 15.x install into a kioskage stick.
#
# Run as root on the device after a base FreeBSD install with networking
# (Ethernet/DHCP) available:
#
#     git clone https://github.com/imush/kioskage
#     cd kioskage && sh provision/install.sh
#
# Idempotent: safe to re-run to pick up updates.
#
set -eu

REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)
PREFIX=/usr/local
KIOSKAGE_HOME=/usr/local/libexec/kioskage
KIOSKAGE_USER=kioskage

log() { echo ">>> $*"; }

[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }

# --------------------------------------------------------------------------
log "Installing packages"
# --------------------------------------------------------------------------
export ASSUME_ALWAYS_YES=yes
pkg update
# Note: hostapd and wpa_supplicant ship in the FreeBSD base system
# (/usr/sbin), so they are not installed as packages here.
pkg install -y \
    python3 \
    chromium \
    xorg-minimal xf86-video-intel xinit openbox unclutter \
    drm-kmod \
    dnsmasq libqrencode \
    avahi-app nss_mdns

# --------------------------------------------------------------------------
log "Installing fonts (comprehensive coverage for kiosk content)"
# --------------------------------------------------------------------------
# xorg-minimal ships almost no fonts, so remote content renders as tofu without
# these. Broad Unicode + web coverage: Noto basic (Latin/Greek/Cyrillic) + extra
# (Hebrew, Arabic, and many more scripts) + color emoji, DejaVu as a fallback,
# and Arial/Times/Courier-compatible faces via Liberation and the MS core
# webfonts. NOTE: the webfonts (MS core fonts) EULA restricts redistribution --
# for prebuilt shipped images, prefer liberation-fonts-ttf alone and drop
# webfonts. Add noto-jp / noto-kr / noto-hk if you need CJK content.
pkg install -y \
    noto-basic noto-extra noto-emoji \
    dejavu liberation-fonts-ttf webfonts

# GPU + wifi firmware
log "Fetching firmware (fwget)"
fwget || true   # pulls iwlwifi/intel firmware if applicable

# --------------------------------------------------------------------------
log "Creating kioskage user"
# --------------------------------------------------------------------------
if ! pw usershow "$KIOSKAGE_USER" >/dev/null 2>&1; then
    pw useradd -n "$KIOSKAGE_USER" -m -s /usr/sbin/nologin -c "kioskage display"
fi
# The kiosk user needs access to the GPU and input devices
pw groupmod video -m "$KIOSKAGE_USER" 2>/dev/null || true
pw groupmod wheel -m "$KIOSKAGE_USER" 2>/dev/null || true

# --------------------------------------------------------------------------
log "Installing kioskage files to $KIOSKAGE_HOME"
# --------------------------------------------------------------------------
mkdir -p "$KIOSKAGE_HOME"
cp -R "$REPO_DIR/portal" "$KIOSKAGE_HOME/"
cp "$REPO_DIR/etc/xinitrc" "$KIOSKAGE_HOME/xinitrc"
cp "$REPO_DIR/etc/kioskage-session" "$KIOSKAGE_HOME/kioskage-session"
chmod +x "$KIOSKAGE_HOME/kioskage-session"
cp "$KIOSKAGE_HOME/xinitrc" "/home/$KIOSKAGE_USER/.xinitrc"
chown -R "$KIOSKAGE_USER:$KIOSKAGE_USER" "/home/$KIOSKAGE_USER"

# symlink the control CLI so it's on PATH
ln -sf "$KIOSKAGE_HOME/portal/kioskagectl.py" "$PREFIX/sbin/kioskagectl"
chmod +x "$KIOSKAGE_HOME/portal/kioskagectl.py"

# Neutral brand profile so the portal has a brand to render out of the box. A
# private overlay (brand.conf + logo) is layered on top later via OTA (see the
# overlay note at the end). Don't clobber an existing one on re-install.
[ -f "$PREFIX/etc/kioskage-brand.conf" ] \
  || cp "$REPO_DIR/etc/brand.conf" "$PREFIX/etc/kioskage-brand.conf"

# --------------------------------------------------------------------------
log "Installing rc.d services"
# --------------------------------------------------------------------------
cp "$REPO_DIR/rc.d/kioskage" "$PREFIX/etc/rc.d/kioskage"
cp "$REPO_DIR/rc.d/kioskage_portal" "$PREFIX/etc/rc.d/kioskage_portal"
chmod +x "$PREFIX/etc/rc.d/kioskage" "$PREFIX/etc/rc.d/kioskage_portal"

# --------------------------------------------------------------------------
log "Configuring X server for the kiosk user"
# --------------------------------------------------------------------------
# Xorg.wrap restricts X to the console user by default; our kiosk starts X as
# the kioskage user via daemon(8), so allow it and let the setuid wrapper grant
# the root rights X needs (there is no seat manager on this minimal install).
mkdir -p /usr/local/etc/X11
printf 'allowed_users=anybody\nneeds_root_rights=yes\n' \
    > /usr/local/etc/X11/Xwrapper.config

# --------------------------------------------------------------------------
log "Configuring rc.conf"
# --------------------------------------------------------------------------
sysrc kld_list="i915kms"
sysrc kioskage_enable="YES"
sysrc kioskage_portal_enable="YES"
sysrc dbus_enable="YES"
sysrc avahi_daemon_enable="YES"
sysrc moused_enable="NO"
sysrc syslogd_flags="-ss"

# Boot-time trim (config-level; no custom kernel): no mail agent, no crash
# dumps. autoboot_delay is kept at 3s (not 0) so kernel.old stays selectable at
# the console after a bad kernel update.
sysrc sendmail_enable="NONE" sendmail_submit_enable="NO" \
      sendmail_outbound_enable="NO" sendmail_msp_queue_enable="NO"
sysrc dumpdev="NO" update_motd="NO"
sysrc -f /boot/loader.conf autoboot_delay="3"

# --------------------------------------------------------------------------
log "Tuning loader.conf"
# --------------------------------------------------------------------------
# Cap the ZFS ARC so it doesn't starve Chromium of RAM. 512M is plenty on a
# 4GB box given the read-only root + tmpfs keep steady-state writes near zero.
# Override before running by exporting ARC_MAX, e.g. ARC_MAX=1024M.
# (sysrc rejects dotted loader tunable names, so write loader.conf directly.)
: ${ARC_MAX:=512M}
if grep -q '^vfs\.zfs\.arc_max=' /boot/loader.conf 2>/dev/null; then
    sed -i '' -E "s|^vfs\.zfs\.arc_max=.*|vfs.zfs.arc_max=\"${ARC_MAX}\"|" \
        /boot/loader.conf
else
    echo "vfs.zfs.arc_max=\"${ARC_MAX}\"" >> /boot/loader.conf
fi

# nss-mdns: make .local resolvable
if ! grep -q mdns /etc/nsswitch.conf 2>/dev/null; then
    sed -i '' -e 's/^hosts:.*/hosts: files dns mdns/' /etc/nsswitch.conf \
        2>/dev/null || \
    printf 'hosts: files dns mdns\n' >> /etc/nsswitch.conf
fi

# tmpfs for volatile state on a read-only root (optional; harmless on rw root)
mkdir -p /var/run/kioskage

# --------------------------------------------------------------------------
log "Installing OTA updater"
# --------------------------------------------------------------------------
# The self-update script + a nightly cron that pulls and applies app updates.
# kioskage itself is public, so the code pulls over HTTPS with no credentials.
#
# To brand a fleet, add a PRIVATE overlay repo (brand.conf + logo), done per
# device out of band (not here):
#   1. generate an ssh key on the stick, register the public half as a
#      read-only deploy key on your overlay repo;
#   2. write /usr/local/etc/kioskage-overlay.conf:
#        KIOSKAGE_OVERLAY_REPO="git@github.com:you/your-overlay.git"
# kioskage-update then pulls the overlay over SSH and layers it on top. With no
# overlay configured the stick runs as plain, unbranded kioskage.
install -m 755 "$REPO_DIR/bin/kioskage-update" "$PREFIX/sbin/kioskage-update"
_cron=$(mktemp)
crontab -l 2>/dev/null | grep -v kioskage-update > "$_cron" || true
echo "0 3 * * * $PREFIX/sbin/kioskage-update --kiosk >/var/log/kioskage-update.log 2>&1" >> "$_cron"
crontab "$_cron"; rm -f "$_cron"

log "Done. Reboot to bring the stick up, or start now with:"
log "  service dbus start && service avahi-daemon start"
log "  service kioskage_portal start"
log "  service kioskage start"
