# Media Library — Home Assistant add-on

A household catalogue of physical and digital media — Blu-ray, 4K, 3D, DVD,
CD, vinyl, cassette and the digital shops you buy from — with per-owner copies,
shared or private visibility, CSV import and export, keyless metadata, and
playable copies of what you own: ripped files on a mounted share, or items held
by a Jellyfin server and matched to the titles you add.

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

- **One title, many copies.** The same film on 3D Blu-ray, on DVD and in a
  digital shop is one title with three copies. Each copy keeps its own format,
  edition, barcode, store, location, condition, price, acquisition date,
  last-watched date, owner, visibility and notes.
- **One owner per copy, shared or private.** Owners are the Home Assistant
  users who open the panel, so the person adding a copy owns it — the add-on
  has no accounts, passwords or invitations of its own. A `shared` copy is
  visible to everyone; a `private` one only to its owner.
- **Stores and platforms are data.** Apple TV, Apple Music, Google TV, YouTube
  Music — or any shop you type — are rows you can add, rename and remove, and a
  shop can be attached to one or more players. A disc records the shop it was
  bought in; a digital copy records the shop it came from.
- **Import and export.** A Blu-ray.com- or iTunes-style CSV is staged for
  review first: map the columns, check each row (ok / duplicate / error), then
  commit, so nothing lands in the catalogue until you say so. Export is a CSV
  spreadsheet of every copy you can see, or a full JSON dump.
- **Keyless metadata.** Films and TV come from Wikidata and Wikipedia (plot,
  poster, year, running time, genres, IMDb id); albums from MusicBrainz
  (release, year, cover); artists from Wikipedia (biography, portrait, links).
  No API key is needed anywhere. A refresh job fills the gaps in the background,
  and a known IMDb or MusicBrainz id always beats a fuzzy name match.
- **Artwork you control.** A pencil on the cover opens every image a lookup has
  offered, with a search across the free cover sources - album editions from
  iTunes and the Cover Art Archive, film posters from Wikipedia - and an upload
  box for a photographed sleeve. A stored MusicBrainz *release* id means that
  edition's own cover, not the standard one's.
- **Browsing.** One mode at a time — Movies, TV, Music, Artists — with an owner
  filter, format pills, search that matches the artist as well as the title,
  list and poster views, and tallies that all count the same thing — titles, so
  a format pill reading 175 beside "Movies 175" is the same 175 films. A search
  also offers the artist or the collection itself as a card, so "alien" gives
  you the series before the six films inside it. Titles file under the first letter of the
  name with *The* / *A* / *An* moved to the end, and numbers sort
  by value (2 before 10).
- **Collections.** A film series - *Fast &amp; Furious* (eleven films, one of
  them *Hobbs &amp; Shaw*), *Alien* - is a row of its own that its titles point
  at, with a page listing them in watching order. A search cannot see a series;
  a collection can. Wikidata's *part of the series* files films into theirs as
  their metadata is refreshed - by itself once a day, and on demand with
  *Refresh metadata* on the admin page, which asks about the series of a film
  whose metadata is already complete without re-fetching any of it - and never
  over a collection you picked. Ask a collection the other way round and it
  lists the films in the series nobody here owns, so the gap in the shelf is
  written down rather than remembered - and *Refresh metadata* on the
  collection itself takes the paragraph and a poster from the series' Wikipedia
  article, so a series page is not just a name until somebody types one. All
  three of those - the film list, the paragraph and the poster - are refreshed
  by the daily pass and by *Refresh metadata* on the admin page as well, and a
  refresh **files** the films it finds: a series that names a film the shelf
  holds but nobody had filed gets it filed for you, while a film you put in
  another collection is left where you put it.
- **A duplicate is a question, not an accident.** Adding a title that is already
  on the shelf points at the existing row, because a second copy belongs there;
  adding a copy someone already owns in that format asks first. The admin page
  carries a duplicate-title merge and a clean pass over rows that no longer
  point at anything.
- **Playback.** Register a Jellyfin server in the admin page and sync: every
  title it holds is matched and recorded as a playable copy, extra versions and
  editions included, and the Play sheet lists the devices that can take it and
  says what the file itself is — `4K · Dolby Vision · DTS-HD MA 5.1 · 73.1 GB`,
  or `FLAC 2.0 · 975 kbps · 27 MB` for an album — so you can see whether the rip
  matches the disc on the shelf before walking over to it. A pinned bar follows
  what is playing — play/pause, stop, previous, next, rewind and fast-forward —
  and tracks a second player in another room as well.
- **A disc gets a player too.** A physical copy has no app to open, but the room
  still has to be set: attach a `media_player`, `remote`, `input_select`,
  `select` (a Harmony Hub's activities) or `automation` to a format
  (Blu-ray, vinyl, …) and the Play sheet
  sets it — one button when the format has one player, a **Player** picker when
  it has several, so three activities on one Harmony Hub are a choice rather than
  three buttons. For a shop, attach a player and give it the app that shop opens
  — an Android package (`com.example.app`) or a name the device itself lists.
- **Barcode scanning.** Type a UPC/EAN or scan it with a phone camera; the
  lookup fills only the boxes you left empty and says so when the code is
  already on a copy. A disc code no free database carries is put to a shop that
  files discs by barcode, which usually answers with the title and year, and then
  to a web search rather than a shrug.

## Reaching it directly (a TV app, Kodi, the barcode scanner)

The add-on publishes its port, so the catalogue answers on the LAN without Home
Assistant in front of it:

| URL | What it is |
| --- | --- |
| `http://<home-assistant>:8099/` | the catalogue, plain HTTP, no login |
| `https://<home-assistant>:8099/` | the same, when a certificate is found |

Home Assistant's own panel keeps working: ingress talks plain HTTP to the same
port, so the server looks at the first byte of each connection and speaks TLS
only to a client that opens with a TLS handshake.

**The barcode scanner needs HTTPS.** Browsers only hand out the camera to a
secure origin, so a plain `http://` page cannot scan — and inside Home
Assistant the panel is framed, which withholds the camera even when Home
Assistant itself is on HTTPS. Open the catalogue over HTTPS (or `localhost`)
and press *Scan* there.

Three ways to get a certificate, in order of preference:

1. **Put it behind Nginx Proxy Manager** (or Caddy, or the reverse proxy you
   already run). Add a proxy host for e.g. `media.example.com` pointing at
   `http://<home-assistant>:8099` and let it issue the Let's Encrypt
   certificate. Renewal is then automatic and nothing has to be copied.
2. **Drop a certificate into the add-on**: `/data/tls/fullchain.pem` plus
   `/data/tls/privkey.pem` — on Home Assistant OS that is
   `/mnt/data/supervisor/addons/data/local_media_collection/tls/`. The server
   picks the unexpired certificate whose name matches the one requested, so a
   renewed certificate only needs the files replaced and the add-on restarted.
3. **Point it at one you already have**: `MC_TLS_CERT` and `MC_TLS_KEY` in the
   add-on environment, or leave the certificate in `/ssl` for Home Assistant
   (the add-on reads `/ssl` read-only).

An **expired** certificate is never served — the add-on logs `https: no
unexpired certificate found …` and stays on plain HTTP, because a browser
warning page would block the camera as surely as HTTP does.

## Repository layout

```
repository.yaml              add-on repository descriptor (the file HA reads)
media_collection/            the add-on
├── config.yaml              ingress, options, media mount
├── Dockerfile / build.yaml  image build (python3 + sqlite, no pip packages)
├── run.sh                   repair → migrate → merge → normalise → serve
├── migrate.sh               transactional migration runner
├── migrations/              the schema, in one baseline file
├── app/                     the web app (stdlib only)
├── DOCS.md                  the Documentation tab in the add-on store
├── translations/en.yaml     option names and help text
└── icon.png / logo.png      store artwork, drawn by tools/make_icons.py

tools/deploy.sh              package and install on a live Home Assistant box
tools/check_addon_repo.py    validate this repository (GitHub Actions runs it)
.github/workflows/           the repository check, on every push and
                             every pull request
```

## Privacy

There is no telemetry and no account of its own. The add-on talks only to the
services you ask it to: MusicBrainz, Wikidata, Wikipedia and a keyless product
API when you add or refresh a title, your own Jellyfin server, and your own Home
Assistant instance (to read its entities and to wake a player).

The database is not part of this repository. The image ships no catalogue, so a
fresh install starts empty and is filled by your own imports or by adding copies
by hand; personal CSVs are git-ignored, so a clone carries no household data. A
Jellyfin API key lives in that database, never in a file the repository tracks.

## Licence

MIT — see [LICENSE](LICENSE).
