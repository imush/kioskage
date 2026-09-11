#!/bin/sh
#
# system-config.sh - system-level settings that must reach sticks ALREADY in the
# field, not only newly provisioned ones.
#
# Sourced by BOTH provision/install.sh (fresh installs) and provision/apply.sh
# (every OTA), so the two cannot drift apart. Everything here must be idempotent
# and safe to re-run on a live, working display.

kioskage_system_config() {
    _prefix="${PREFIX:-/usr/local}"

    # ---- Onboard Realtek rtw88 -------------------------------------------
    # RTL8821C scans but cannot associate on FreeBSD 15 (EOPNOTSUPP at AUTH,
    # upstream-deferred) and enumerates before USB dongles, so it steals wlan0
    # from a working radio. Blocklisting the driver keeps it out of
    # net.wlan.devices entirely.
    #
    # This names ONLY if_rtw88: Intel (iwlwifi/iwm) and Ralink (run) radios are
    # untouched, so it is a no-op on any stick without a Realtek part.
    #
    # Guard: never blocklist a radio the stick is USING RIGHT NOW. If wlan0 is
    # associated through an rtw device and holds an address, then whatever the
    # driver's reputation, it is working here — and blocking it would remove the
    # network at the next boot, after which setup-mode self-heal would retry
    # forever against a radio that no longer exists. An OTA must not be able to
    # strand a working display.
    if ifconfig wlan0 2>/dev/null | grep -q 'parent interface: rtw' &&
       ifconfig wlan0 2>/dev/null | grep -q 'inet '; then
        echo "wlan0 is associated via an rtw radio - leaving if_rtw88 enabled"
    else
        sysrc -n devmatch_blocklist 2>/dev/null | grep -qw if_rtw88 \
            || sysrc devmatch_blocklist+="if_rtw88"
    fi

    # ---- sshd: shut the network door, leave the console open --------------
    # The appliance keeps an EMPTY root password deliberately: the documented
    # recovery is "keyboard, Ctrl+Alt+F1, log in as root", and pw lock would fail
    # password auth at the CONSOLE too, leaving single-user boot as the only way
    # in. Physical access is already full control (the installer USB re-images
    # the disk unattended), so the console is not the boundary - the network is.
    #
    # prohibit-password rather than no: identical today, since no keys are
    # installed and root ssh is impossible either way, but a future management
    # tunnel becomes an authorized_keys drop instead of a config change.
    sysrc sshd_enable="YES" >/dev/null
    _sshd="${SSHD_CONFIG:-/etc/ssh/sshd_config}"   # overridable for tests
    if ! grep -q '^# --- kioskage ---' "$_sshd" 2>/dev/null; then
        # Comment out existing settings first: sshd honours the FIRST occurrence
        # of a keyword, so appending alone would not override one set above.
        sed -i '' -E 's/^[[:space:]]*(PermitRootLogin|PermitEmptyPasswords)[[:space:]]/#&/' "$_sshd"
        printf '\n# --- kioskage ---\nPermitRootLogin prohibit-password\nPermitEmptyPasswords no\n' >> "$_sshd"
        service sshd reload >/dev/null 2>&1 || true
        echo "sshd: root password auth disabled (console login unaffected)"
    fi
}
