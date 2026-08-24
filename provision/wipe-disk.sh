#!/bin/sh
#
# wipe-disk.sh - completely erase a disk's partitioning so a fresh image can be
# written to it. Kills what a naive overwrite leaves behind on ex-Windows mini
# PCs: the recovery/EFI partitions, the PROTECTIVE MBR, and BOTH GPT copies
# (the backup GPT header lives in the LAST sector of the disk and survives
# dd'ing a smaller image over the front - that leftover is what makes firmware
# and gpart see a "corrupt" or hybrid table). Optionally clears stale UEFI boot
# entries (e.g. "Windows Boot Manager") so the box stops trying to boot the OS
# you just erased.
#
# Run as root on a FreeBSD system booted from OTHER media (e.g. a USB installer)
# than the disk you're wiping.
#
#   sh wipe-disk.sh <device>       # e.g. mmcsd0, ada0, nvd0, da1
#   sh wipe-disk.sh --auto         # pick the single disk that isn't the root disk
#   sh wipe-disk.sh --auto -y -e   # non-interactive + also clear Windows UEFI entries
#
# Flags:
#   --auto   choose the only non-root disk automatically (fails if 0 or >1)
#   -y       don't prompt for confirmation (for scripted installers)
#   -e       also delete UEFI boot entries whose label contains "Windows"
#   -n       dry run: show what would happen, touch nothing
#
set -eu

AUTO=no; YES=no; EFI=no; DRY=no; DEV=""
for a in "$@"; do
    case "$a" in
        --auto) AUTO=yes ;;
        -y) YES=yes ;;
        -e) EFI=yes ;;
        -n) DRY=yes ;;
        -*) echo "unknown flag: $a" >&2; exit 2 ;;
        *)  DEV="$a" ;;
    esac
done

[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
[ "$(uname -s)" = FreeBSD ] || { echo "FreeBSD only (uses gpart/diskinfo)"; exit 1; }

run() { if [ "$DRY" = yes ]; then echo "[dry-run] $*"; else "$@"; fi; }

# ── Which physical disks back the running root? Never wipe those. ─────────────
# Mirrors install.sh's root-pool guard: resolve / to its pool (ZFS) or device
# (UFS), then to base disk names. This is the safety net that stops you erasing
# the USB you booted from.
protected_disks() {
    rootsrc=$(mount -p 2>/dev/null | awk '$2=="/"{print $1; exit}')
    case "$rootsrc" in
        /dev/*)   # UFS: /dev/da0s1a -> da0
            basename "$rootsrc" | sed -E 's/(s[0-9]+[a-z]?|p[0-9]+)$//' ;;
        *)        # ZFS: dataset -> pool -> member disks
            pool=${rootsrc%%/*}
            zpool status "$pool" 2>/dev/null \
              | grep -oE '(mmcsd|ada|nvd|nda|da|vtbd)[0-9]+' | sort -u ;;
    esac
}

PROTECTED=$(protected_disks)
DISKS=$(sysctl -n kern.disks 2>/dev/null)

# ── Resolve the target ───────────────────────────────────────────────────────
if [ "$AUTO" = yes ]; then
    [ -z "$DEV" ] || { echo "give a device OR --auto, not both" >&2; exit 2; }
    cand=""
    for d in $DISKS; do
        skip=no
        for p in $PROTECTED; do [ "$d" = "$p" ] && skip=yes; done
        [ "$skip" = no ] && cand="$cand $d"
    done
    set -- $cand
    if [ "$#" -ne 1 ]; then
        echo "auto-detect needs exactly one non-root disk; found: [$cand]" >&2
        echo "root disk(s): [$PROTECTED]   all disks: [$DISKS]" >&2
        echo "re-run naming the target explicitly." >&2
        exit 1
    fi
    DEV="$1"
fi

[ -n "$DEV" ] || { echo "usage: $0 <device> | --auto   [-y] [-e] [-n]"; \
                   echo "disks: $DISKS  (root: $PROTECTED)"; exit 2; }
DEV=$(basename "$DEV")                     # accept mmcsd0 or /dev/mmcsd0
[ -c "/dev/$DEV" ] || { echo "no such disk: /dev/$DEV" >&2; exit 1; }

for p in $PROTECTED; do
    [ "$DEV" = "$p" ] && { echo "REFUSING: /dev/$DEV backs the running root"; exit 1; }
done

# ── Show it and confirm ──────────────────────────────────────────────────────
SIZE=$(diskinfo "/dev/$DEV" 2>/dev/null | awk '{print $3}')   # media size, bytes
GB=$(( ${SIZE:-0} / 1000000000 ))
echo "Target: /dev/$DEV  (~${GB} GB)"
echo "Current partitioning:"
gpart show "/dev/$DEV" 2>/dev/null || echo "  (no partition table / unreadable)"
echo

if [ "$YES" != yes ] && [ "$DRY" != yes ]; then
    printf "This ERASES ALL DATA on /dev/%s. Type ERASE to proceed: " "$DEV"
    read ans
    [ "$ans" = ERASE ] || { echo "aborted."; exit 1; }
fi

# ── Wipe ─────────────────────────────────────────────────────────────────────
echo ">>> destroying partition scheme on /dev/$DEV"
# gpart destroy fails if there's no scheme; that's fine.
if [ "$DRY" = yes ]; then echo "[dry-run] gpart destroy -F /dev/$DEV";
else gpart destroy -F "/dev/$DEV" 2>/dev/null || true; fi

echo ">>> zeroing first 1 MiB (protective MBR + primary GPT + boot code)"
run dd if=/dev/zero of="/dev/$DEV" bs=1m count=1

if [ -n "${SIZE:-}" ] && [ "$SIZE" -gt 2097152 ]; then
    LAST=$(( SIZE / 1048576 - 1 ))         # last whole MiB
    echo ">>> zeroing last 1 MiB at MiB $LAST (backup GPT header + array)"
    run dd if=/dev/zero of="/dev/$DEV" bs=1m oseek="$LAST" count=1
else
    echo "!! couldn't read media size; skipping tail wipe - backup GPT may remain" >&2
fi

# ── Optional: clear stale UEFI boot entries for the erased OS ─────────────────
if [ "$EFI" = yes ]; then
    if command -v efibootmgr >/dev/null 2>&1; then
        echo ">>> removing UEFI boot entries matching 'Windows'"
        # efibootmgr lines look like: BootXXXX* Windows Boot Manager
        efibootmgr 2>/dev/null | grep -iE '^Boot[0-9A-F]{4}.* Windows' | \
        while read -r line; do
            num=$(echo "$line" | sed -nE 's/^Boot([0-9A-Fa-f]{4}).*/\1/p')
            [ -n "$num" ] && { echo "   deleting Boot$num"; \
                run efibootmgr -B -b "$num" >/dev/null 2>&1 || true; }
        done
    else
        echo "!! efibootmgr not found; skipping UEFI entry cleanup" >&2
    fi
fi

echo
echo ">>> done. /dev/$DEV now shows:"
gpart show "/dev/$DEV" 2>/dev/null || echo "  (empty - no partition table, as intended)"
