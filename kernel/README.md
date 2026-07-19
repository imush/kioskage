# Kernel

Two levels of "make the stick leaner/faster to boot", safe-first.

## Phase 1 — config-level boot trim (done, no custom kernel)

Applied by `provision/install.sh`, zero brick risk, most of the practical
benefit:

- `sendmail_enable="NONE"` (+ submit/outbound/msp off) — no mail agent at boot
- `dumpdev="NO"` — no crash-dump device
- `update_motd="NO"` — skip motd rebuild
- `autoboot_delay="3"` — 3s boot-menu wait instead of 10s (kept **non-zero** so
  `kernel.old` stays selectable at the console after a bad kernel)
- already: `moused_enable="NO"`, `syslogd_flags="-ss"`

No custom kernel, so this rides FreeBSD binary/pkgbase updates with no rebuild.

## Phase 2 — custom minimal kernel (`KERNCONF-HIGOLE-J3455`, draft)

Hardware-specific (naming: `KERNCONF-<VENDOR>-<MODEL>`). Derived from GENERIC by
removing hardware the J3455 kioskage stick never has, keeping only what it needs
to boot and run: ACPI, USB (HID + storage), the **eMMC/`mmcsd` root**, Realtek
Ethernet, the 802.11 stack, `evdev`, the `vt`/`vt_efifb` console, `bpf`, and the
LinuxKPI infrastructure `i915kms` needs. GPU (`i915kms`), onboard WiFi
(`if_iwm`) and `zfs` stay **modules**.

Build/install/fallback steps are in the header of
[`KERNCONF-HIGOLE-J3455`](KERNCONF-HIGOLE-J3455).

### Two things to know before flipping the default kernel

1. **A bad kernel can't be OTA-recovered.** If a custom kernel doesn't boot,
   `kioskage-update` can't help — recovery needs **physical console access** to
   pick `kernel.old` / `kernel.generic` from the loader menu. Always keep a
   named-good fallback kernel installed, and test at the console.
2. **It complicates updates.** A custom `KERNCONF` must be rebuilt from source
   on every FreeBSD update and does not ride binary/pkgbase updates cleanly —
   the opposite of what the OTA pipeline wants. Decide whether the boot-time /
   footprint win is worth that ongoing cost for the fleet.

### Installing on a pkgbase system (FreeBSD 15+)

The base is package-managed, so `make installkernel` **refuses to overwrite the
packaged `/boot/kernel`** (it would desync the pkg database). Instead, install
the custom kernel under a different name and boot it via the loader — this keeps
the packaged kernel pristine, pkg-consistent, and still receiving base updates as
a fallback:

```
make installkernel KERNCONF=HIGOLE-J3455 INSTKERNNAME=kernel.custom
sysrc -f /boot/loader.conf kernel="kernel.custom"
```

`i915kms` still loads from `/boot/modules` (drm-kmod package); base modules
(`zfs`, `snd_hda`, `if_iwm`, …) come from `/boot/kernel.custom`.

### Build gotchas hit while trimming (all caught by `buildkernel`, pre-install)

- **Sound:** don't drop the `sound` framework while leaving any `snd_*` driver —
  they need the generated `channel_if.h`. And **keep `snd_hda`**: HDMI audio on
  this Intel platform rides the HDA bus (GPU HDMI codec), so `snd_hda` is
  required for any future audio-over-HDMI content. Only the discrete PCI sound
  cards are removed.
- **IPSEC:** keep `IPSEC_SUPPORT` — removing it fails to link NIC drivers with
  IPSEC offload (`mlx5en` references `ipsec_accel_*`).

Recommendation: ship on **Phase 1**; treat Phase 2 as an opt-in, per-hardware,
console-tested build — not something pushed to remote sticks via OTA.
