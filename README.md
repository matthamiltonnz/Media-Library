# Media Library — Home Assistant add-on

A household catalogue of physical and digital media (Blu-ray, 4K, 3D, DVD, iTunes,
ripped files, CDs, vinyl) with per-owner copies, shared visibility, CSV import and
export, keyless metadata, and playback-source records for the files on your NAS.

Runs as a Home Assistant add-on (ingress), backed by one SQLite file in `/data`.

## Install

1. Home Assistant → **Settings → Add-ons → Add-on store → ⋮ → Repositories**
2. Add `https://github.com/matthamiltonnz/Media-Library`
3. Install **Media Collection**, start it, open it from the sidebar.

Or copy `media_collection/` into `/addons/` and use the local add-on store
(**Reload** → *Local add-ons* → Install).

Full documentation: [media_collection/DOCS.md](media_collection/DOCS.md).

## What it does

- **One title, many copies** — a film owned on 3D Blu-ray and iTunes is one title
  with two copies, each with its own format, barcode, location, condition, owner,
  visibility and last-watched date.
- **Multi-user without logins** — Home Assistant ingress passes the user id, so the
  person signed in owns whatever they add; shared copies are visible to everyone,
  private ones only to their owner.
- **Import / export** — Blu-ray.com and iTunes style CSVs go through a staged review
  (map columns → check ok/duplicate/error → commit). Export is CSV or JSON.
- **Metadata** — keyless (Wikidata + Wikipedia): plot, poster, year, IMDb id, with a
  background refresh for the whole library and IMDb-id-first matching so a corrected
  match sticks.
- **Browsing** — one mode at a time (**Movies**, **TV**, **Music**), never a mixed
  list, plus an owner filter for *Everyone* or a single person, with titles/copies
  counts broken down by format, search, list and poster views, and the admin page
  tucked away at the foot of the catalogue.
- **Playback sources** — record that a copy is a ripped file on the NAS (root + path),
  or a Jellyfin/Kodi/iTunes item, ready for routing later. A ripped music tree
  (`Owner's Music/Artist/Album`) is scanned on every start, turning each album folder
  into a CD copy with a rip source.
- **Housekeeping** — automatic schema migrations, duplicate-title folding, and a
  repair pass that survives interrupted upgrades.

## Repository layout

```
repository.yaml              add-on repository descriptor
media_collection/            the add-on
├── config.yaml              ingress, options, media mount
├── Dockerfile / build.yaml  image build (python3 + sqlite, no pip packages)
├── run.sh                   repair → migrate → merge → normalise → serve
├── migrate.sh               transactional migration runner
├── migrations/              0001…0005
├── app/                     the web app (stdlib only)
└── seed/                    optional bundled catalogue (see seed/README.md)
```

## Privacy

The database is not part of this repository. `seed/media.db` and personal CSVs are
git-ignored, so a clone starts empty and imports your own exports.

## Licence

MIT — see [LICENSE](LICENSE).
