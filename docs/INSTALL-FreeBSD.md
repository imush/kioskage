# Installing FreeBSD on the kioskage stick

This is the hands-on, at-the-device part. Once FreeBSD is installed and on the
network, `provision/install.sh` does the rest.

Target for the prototype: **HIGOLE J3455** (Intel Celeron J3455, Apollo Lake,
x86_64, Intel HD Graphics 500 → `i915kms`). Production target: **MeLE PCG02
(Intel N100)**. Both are ordinary amd64 PCs — no ARM/UEFI quirks.

## 1. Create the install USB (from macOS)

Download the memstick image and verify:

```sh
cd ~/Downloads
URL=https://download.freebsd.org/releases/amd64/amd64/ISO-IMAGES/15.1
curl -LO $URL/FreeBSD-15.1-RELEASE-amd64-memstick.img.xz
xz -d FreeBSD-15.1-RELEASE-amd64-memstick.img.xz
```

Find the USB disk, unmount it, and write the image (**double-check the disk
number — `dd` to the wrong disk destroys it**):

```sh
diskutil list                       # identify the USB, e.g. /dev/disk4
diskutil unmountDisk /dev/disk4
sudo dd if=FreeBSD-15.1-RELEASE-amd64-memstick.img of=/dev/rdisk4 bs=1m
diskutil eject /dev/disk4
```

## 2. Boot the stick from USB

- Plug in USB keyboard, HDMI, **Ethernet**, and the install USB.
- Power on, tap the BIOS key (usually `Del`/`Esc`/`F7` on these mini PCs).
- Disable Secure Boot if present; set the USB as first boot device.
- Boot the FreeBSD installer.

## 3. Run the installer (bsdinstall)

- Keymap: default.
- Hostname: anything (the portal resets it later, e.g. `kiosk-a1b2c3`).
- Distribution sets: `kernel` + `base` are enough (add `lib32` if unsure).
- Partitioning: **Auto (ZFS)** → single disk → default.
- Root password: set one (admin/SSH only).
- Network: pick the Ethernet interface → **IPv4 → DHCP → Yes**. Skip IPv6.
- Resolver: accept defaults.
- Time zone: your local zone.
- Services to start at boot: enable **sshd** (and **ntpd**).
- Add a user: create an admin user, add to group `wheel`.
- Exit → drop to a shell (or reboot and log in).

## 4. First boot: get the code and provision

Log in as root (or `su`), make sure Ethernet has an IP (`ifconfig`), then:

```sh
pkg install -y git            # bootstraps pkg on first use
git clone https://github.com/imush/kioskage-stick
cd kioskage-stick
sh provision/install.sh
```

`install.sh` installs Chromium + X + the WiFi/AP tooling, creates the `kioskage`
user, installs the services, and wires up `rc.conf`. Then:

```sh
reboot
```

## 5. Configure via the portal

After reboot the stick brings up Ethernet and starts the setup portal on
port 80. From any machine on the same network open:

- `http://<stick-ip>/`  (find the IP with `ifconfig` on the device), or
- `http://<hostname>.local/` once mDNS is up.

Set the network, device name and content URL, hit **Connect & Save**, then
**Start**. The chosen URL opens full-screen on the HDMI output.

## 6. WiFi (when the dongle arrives)

For shipped units with no Ethernet, the stick falls back to an **AP + captive
portal**: it broadcasts an open `kiosk-xxxxxx` network; join it from a phone and
the portal opens automatically. Requirements:

- A `run(4)`-compatible dongle (Ralink RT5370/RT5372) for AP mode.
- `net.wlan.devices` must list a wifi device (`sysctl net.wlan.devices`).

Note: one radio can't reliably do AP and client at once on FreeBSD — the AP is
torn down the moment a client connection succeeds. If both onboard wifi and a
dongle are present, the dongle (last device) is used for the AP.

## Troubleshooting

- No GPU / black screen: confirm `kldstat | grep i915` shows the module; check
  `sysrc kld_list` includes `i915kms`.
- `.local` not resolving: `service avahi-daemon status`, and ensure
  `/etc/nsswitch.conf` has `hosts: files dns mdns`.
- Portal unreachable: `service kioskage_portal status`; it listens on `:80`.
- Kiosk not starting: `service kioskage status`; run `kioskagectl status` for a
  full JSON dump of network/kiosk state.
