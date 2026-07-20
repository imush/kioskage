# Migrating a stick from `signage-stick` to `kioskage`

The legacy private `signage-stick` install becomes an kioskage install (public
code + private brand overlay). The migration **logic** is
[`provision/migrate-from-signage-stick.sh`](../provision/migrate-from-signage-stick.sh)
here in kioskage; the old repo only carries a tiny **one-time trigger**.

> **Test on a throwaway stick first.** Provision one the old way, run the
> migration, confirm it comes up on kioskage and updates from the new repos —
> *then* consider migrating a real (NAT'd, unreachable) stick.

## Per-stick prerequisite (do once, on GitHub — no stick access needed)

The overlay is private, so the stick pulls it over SSH. Register the stick's
**existing deploy-key public half** (already a read-only deploy key on
`signage-stick`) as a read-only deploy key on `kioskage-hebrewcalendar` too.

## How the handoff works

A stick still running `signage-stick` pulls its repo nightly via `signage-update`
→ `apply.sh`. Push **one** commit to the (otherwise frozen) `signage-stick`
`main` that adds a guarded trigger to its `provision/apply.sh`:

```sh
# --- one-time kioskage migration (remove once the fleet has moved) ---
# Guarded on the SUCCESS marker (written only once kioskage is proven healthy),
# not on kioskage.conf — so a migration that fails partway can be retried by
# simply re-pushing this trigger; it won't be skipped mid-migration.
if [ ! -f /usr/local/etc/kioskage-migrated ]; then
  rm -rf /usr/local/src/kioskage
  git clone --depth 1 https://github.com/imush/kioskage.git /usr/local/src/kioskage
  sh /usr/local/src/kioskage/provision/migrate-from-signage-stick.sh \
     git@github.com:imush/kioskage-hebrewcalendar.git
fi
```

On the next `signage-update` the stick clones kioskage (public, HTTPS) and runs
the migration, which:

1. copies `signage.conf` → `kioskage.conf` (same format),
2. writes the overlay repo into `kioskage-overlay.conf`,
3. runs kioskage's `install.sh` (kioskage user, files, neutral `brand.conf`,
   services, OTA cron), then pulls + applies the overlay,
4. cuts `:80` over to kioskage and **proves it healthy — both the portal AND,
   if a display was running, the real screen (Xorg + Chromium)** — before
   touching anything irreversible,
5. only then writes the `kioskage-migrated` marker and retires the legacy
   `signage` services + `signage-update` cron,
6. reboots into kioskage.

**Fail-safe.** Until step 5, an `EXIT` trap fully restores the signage stack
(screen + portal) on *any* error, so a migration that fails at any point leaves
the stick alive on signage exactly as it was — it never retires signage in
favour of a portal-only (dark-screen) kioskage, and never strands the stick
half-migrated. Signage files stay installed (only disabled) after cutover as a
last-resort manual recovery path.

Because it's guarded on the `kioskage-migrated` marker (written only on success),
it runs exactly once on success; after the reboot the old `signage-update` cron
is gone, so the trigger never fires again. If it ever aborts, re-push the trigger
to retry.

## After the fleet has migrated

Drop the trigger commit (and archive `signage-stick`). kioskage sticks now
update themselves from `kioskage` + the overlay via `kioskage-update`.
