# Media Library — Home Assistant add-on

A household catalogue of physical and digital media (Blu-ray, 4K, 3D, DVD, iTunes,
CDs, vinyl) with per-owner copies, shared visibility, CSV import and export, keyless
metadata, and playable digital backups of what you own — ripped files mounted into
Home Assistant or held by a Jellyfin server, matched to the titles you add.

Runs as a Home Assistant add-on (ingress), backed by one SQLite file in `/data`.

## Install

This repository is a Home Assistant **add-on repository**: add its address once
and **Media Collection** appears in the add-on store like any other add-on.

[![Add the repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fmatthamiltonnz%2FMedia-Library)

[![Open Media Collection in Home Assistant](https://my.home-assistant.io/badges/supervisor_app.svg)](https://my.home-assistant.io/redirect/supervisor_app/?app=media_collection&repository_url=https%3A%2F%2Fgithub.com%2Fmatthamiltonnz%2FMedia-Library)

The first badge adds the repository, the second opens the add-on once it is
there. Both go through [My Home Assistant](https://my.home-assistant.io/), so
they only work from a browser that can reach your instance; otherwise use the
manual steps below.

### By hand

1. Home Assistant → **Settings → Add-ons → Add-on store → ⋮ → Repositories**
2. Paste `https://github.com/matthamiltonnz/Media-Library` and press **Add**
3. Close the dialog — the store reloads and lists **Media Collection** under
   *Media Library Add-ons*
4. Open it, press **Install**, then **Start**, then open **Media Collection**
   in the sidebar

### Requirements

- Home Assistant OS or Home Assistant Supervised — add-on repositories need the
  Supervisor, so Home Assistant Container cannot install this
- `amd64` (x86-64), `aarch64` (64-bit ARM: Home Assistant Green, Yellow,
  Raspberry Pi 4/5) or the 32-bit `armv7` / `armhf` / `i386` images
- internet access on the Home Assistant machine for the first install: the
  Supervisor builds the image from a Home Assistant base image plus Alpine's
  `python3` and `sqlite`. No prebuilt image is published, so the first install
  takes a few minutes (longer on a Pi) and an update rebuilds the image again.
- `/media` is mounted read-only (`media:ro`) so the add-on can find ripped
  files; the catalogue itself is `/data/media.db` inside the add-on

Nothing else is needed to install. A fresh start opens an empty catalogue in the
sidebar, and copies are added by hand or by CSV import.

### A local copy instead (development)

Copy `media_collection/` into `/addons/` and press **Reload** in the add-on
store; it then installs from *Local add-ons*. Bump `version` in `config.yaml`
for each change — the Supervisor only rebuilds when that changes. Both routes
use the same slug (`media_collection`), so a machine should have one or the
other, never both.

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
- **Playback sources** — a ripped copy is a playable digital backup of media you
  already own, never a title of its own: it becomes another copy of a title in the
  collection and records the media root plus the path inside a folder mounted into
  Home Assistant (or the Jellyfin/Kodi/iTunes item). A ripped music tree
  (`Owner's Music/Artist/Album`) is matched on every start, so an album folder
  becomes the playable copy of an album you already catalogued.
- **Housekeeping** — automatic schema migrations, duplicate-title folding, and a
  repair pass that survives interrupted upgrades.

## Repository layout

```
repository.yaml              add-on repository descriptor (the file HA reads)
media_collection/            the add-on
├── config.yaml              ingress, options, media mount
├── Dockerfile / build.yaml  image build (python3 + sqlite, no pip packages)
├── run.sh                   repair → migrate → merge → normalise → serve
├── migrate.sh               transactional migration runner
├── migrations/              the schema, one file per change
├── app/                     the web app (stdlib only)
├── DOCS.md                  the Documentation tab in the add-on store
├── translations/en.yaml     option names and help text
└── icon.png / logo.png      store artwork, drawn by tools/make_icons.py

tools/deploy.sh              package and install on a live Home Assistant box
tools/check_addon_repo.py    validate this repository (GitHub Actions runs it)
```

## Privacy

The database is not part of this repository, and the image ships no catalogue at all:
a fresh install starts empty and is filled by your own imports or by adding copies by
hand. Personal CSVs are git-ignored too, so a clone carries no household data.

## Licence

MIT — see [LICENSE](LICENSE).
