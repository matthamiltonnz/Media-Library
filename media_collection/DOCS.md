# Media Collection — Home Assistant add-on

A **catalogue** of what your household owns, plus the routing metadata needed to
play it. The database is the source of truth; Jellyfin and Kodi stay responsible
for the files they already manage.

## Model

```
titles 1───n items 1───n digital_sources
                  └── playback_targets (devices, not tied to one item)
```

- **titles** — the work: `name`, `release_year`, `kind` (movie/tv/other), `tmdb_id`, `imdb_id`.
- **items** — the copy you own: owner, `format`, `edition`, `upc_ean`, `region`,
  `bluray_com_id`, `condition`, `location`, `visibility`.
- **digital_sources** — how to reach that copy in a library: `provider`
  (itunes/appletv/jellyfin/kodi/other), `library_id`, `external_id`.
- **playback_targets** — the devices a later "play this" action will drive:
  `kind` (bluray_player/apple_tv/jellyfin/kodi), `ha_entity_id`, `input_source`, `app_name`.

One title can therefore have a Blu-ray, an iTunes purchase and a Jellyfin file
without them counting as duplicates.

## Library admin

The catalogue page carries a small **admin** link beside its title. That page holds the
occasional jobs:

- **Import a CSV** — staged review, as described above.
- **Export** — `export.csv` (every copy you can see, UTF-8 with BOM so Excel opens
  it cleanly) and `export.json` (the same rows as a backup-friendly document).
- **Maintenance** — the bundled-catalogue merge, with a count of anything still
  missing.

Exports are per viewer: shared copies plus your own private ones.

- **Metadata** — *Refresh all metadata* walks every title in the background
  (sequential, two requests per title: Wikipedia page search + summary) and fills
  plot, poster, year and IMDb id. The page polls `/admin/status.json` every three
  seconds while it runs, then reloads itself when the job stops. The "also
  overwrite" box forces a re-fetch for titles that already have metadata.
  Matching is **id first**: an IMDb id (`P345`) or a TMDB id (`P4947` for films,
  `P4983` for TV) is resolved through Wikidata, which also supplies the year and
  the enwiki page — so a hand-entered id always wins over a name search. Without
  an id the Wikipedia search is disambiguated by the title's year, which keeps
  *3:10 to Yuma* (1957) and *3:10 to Yuma* (2007) apart. Editing the year, IMDb id
  or TMDB id on the title page clears the cached plot/poster and re-resolves at
  once; an id that Wikidata doesn't know is reported back to you. *Refresh all*
  also revisits any title whose stored match did not come from the id on record.
  Wikimedia rate-limits bursts with HTTP 429: the job then **pauses** and the button
  becomes *Resume refresh* — press it later and it continues where it stopped,
  since it only targets titles still missing metadata.

The catalogue opens in **Thumbnails** view (poster grid, one card per title with its
formats and copy count); the **List view** link next to the search box switches to
the table. The choice is carried through searches.

"Add a copy" takes an optional **IMDb ID**; tick *Look up metadata* and the new
title is created with its plot/poster/year straight away, landing you on its
detail page.

## Copies

The **Copies** tab holds the detail and the editing: every copy of the title with
its format, store, owner, edition, region, shelf, condition, barcode, price paid,
purchase date, last watched date and visibility. Open a copy to change it, delete
it, or add another one (owner included) without leaving the page. A digital copy
records the store it was bought from - iTunes, Apple TV or other - next to what it
cost and when; Kodi is no longer offered because it sits outside Home Assistant.

## By artist

Music titles are stored as "Artist — Album", and that name is split once into an
`artists` row per act (`artists.py --link`, re-run on every add-on start and after
a music import). The Music view then offers **By artist**: every act with albums
on the shelf, how many, and the years covered, as a table or as a wall of covers.
Each act opens a page with their albums (again list or thumbnails), what each one
is owned as, and a MusicBrainz link, so a music title edits the **MusicBrainz ID**
instead of the IMDb/TMDb pair.

Artist details come from MusicBrainz without any Jellyfin involvement: **Admin →
Artists → Fill artist details** looks up every act that has no id yet (one request
a second, in the background) and stores the MusicBrainz id, country and the year
the act started; a few are filled in automatically on each start. Since an artist
has no picture of its own, the page and the cover wall borrow the art of their
first album - Jellyfin's image when that album is linked, otherwise the Cover Art
Archive picture MusicBrainz gave it.

## Filtering

The format tally under the heading ("Blu-ray 172 · Digital 169 · 4K Blu-ray 38") is
clickable: press a format to see only those copies, press the same one again to
clear it. The tallies keep showing the whole library so you can switch between
them. Every card and list row carries the filters it was opened with, so **Back to
the catalogue** returns to the same kind, owner, search and format.

## Ripped files are copies too

A file found on the NAS becomes its own copy of the title, badged **Ripped file ·
whoever owns the disc**, so a library of nothing but files still fills the shelf and
a disc keeps its own row untouched. Old scans had hung the file off whichever copy
came first, which hid it; the scan now moves those onto their own copy
(`repoint_rips`) on every run.

Because the shelf is for managing discs, titles whose only copies are files are
hidden by default - the link under the tallies (*Show titles that are only ripped
files*) brings them back, and the **Ripped file** tally does the same with a click.

## Play

A title page opens on **About**: one badge per copy (format, store and owner, so
"Blu-ray · Matt" and "Digital · iTunes · Mitchell" read at a glance), then a single
**Play** button. That button opens a small window with two labelled halves:
**From the shelf** picks a Home Assistant player and then the copy to start, and
**On the Jellyfin server** picks the Jellyfin app that is open right now (Apple TV,
Vero, a phone) and starts the linked item there. Nothing lists sessions on the page
itself - the picker is where they belong. Only players that accept a file
(`play_media`) and are reachable are listed, and **Admin → Players** narrows that
list to the handful you actually use.

Playback asks Home Assistant to play
`media-source://media_source/local/<path under /media>` through
`media_player.play_media`, so HA resolves the file itself and hands the player
whatever it understands (cast, DLNA, Kodi…). HA only resolves files, not folders,
so a source that points at an album or a film folder plays its first track and
queues the rest where the player has a queue. Errors come back in the red banner.
No streaming server runs in the add-on.

That needs `homeassistant_api: true` in `config.yaml`; the token is read from
`/run/s6/container_environment/SUPERVISOR_TOKEN` because the base image starts the
service with a stripped environment. The last player used is remembered in the
`settings` table, and failures are reported back on the title page.

## Playback sources (rips on the NAS)

Each copy can record where it can be played from: open a title → **Playback
sources**. Pick the copy, the provider (*Ripped file (NAS)*, Jellyfin, Kodi, iTunes,
Apple TV) and the **NAS root** plus the path inside it.

Roots are rows, not config — Admin → **Media roots** lists them, and every row is
editable in place (label and path) with **Save**, alongside **Remove**; a new
folder is an **Add** form underneath. Labels and paths must be unique, and a clash is
reported rather than silently dropped. Seeded with:

| Root | Path |
|---|---|
| Matt — Movies | `/media/My Media/QNAP/Movies` |
| iTunes Movies | `/media/My Media/QNAP/iTunes Movies` |
| TV Shows | `/media/My Media/QNAP/TV` |
| Mitchell — Movies | `/media/My Media/QNAP/Mitchell's Movies` |

The add-on mounts the Home Assistant media share read-only (`map: media:ro`), so
those paths are what the container sees. Nothing is scanned or moved — the path is
stored so the future "play this" action has a target.

## Music: CDs and vinyl

`format` covers `cd`, `vinyl`, `sacd` and `cassette` alongside the video formats,
and `kind` adds **music** — so an album is a title with one row per copy (CD, vinyl,
or a ripped FLAC source on the music root). The CSV importer maps "CD", "Audio CD",
"Vinyl", "LP", "SACD", "Cassette"/"Tape" automatically.

### Album metadata from MusicBrainz

Music titles are resolved by **MusicBrainz** instead of Wikipedia: the title name is
split into artist and album on the em dash (`ABBA — Gold (1992)`), the release-group
search is scored on title similarity plus the stored year, and the winning group
supplies the first release date, the album type and the release-group id (stored in
`musicbrainz_id`, linked from the title page). Cover art comes from the Cover Art
Archive — only images that actually exist are stored, served over HTTPS so the
ingress page doesn't block them.

Two requests per album at MusicBrainz's one-per-second limit, so a full first pass
over a few hundred albums takes roughly half an hour; the job pauses and resumes on
`429`/`503` like the film lookups do. There is no API key to configure. Albums with
no MusicBrainz match are left untouched and reported back on the title page.

### Bonus video discs and vinyl

A CD that came with a bonus DVD is two copies of one title: the FLAC files give the
**CD** copy, and the videos under `Music/Videos/<Artist>/<Album>` are attached by hand
as a **DVD** copy (`edition` = *Bonus video disc*) with its own rip source, because the
folder layout there doesn't say which album a disc belongs to. Vinyl is recorded as a
**Vinyl** copy pointing at its rip (`edition` = *Vinyl rip · MP3* when the files are
lossy) — the scanner can only see the codec, not the plastic.

### Bulk import of a ripped music tree

`app/music_import.py` walks `<root>/<Owner's Music>/<Artist>/<Album>` and creates one
music title per album (named `Artist — Album`, year lifted from a `(1999)` suffix),
one `cd` copy owned by the person whose folder it sits in, and a `rip` playback
source pointing at the album folder. The codec decides the copy: **FLAC → CD** (a rip),
**MP3 → Digital · Google Play · MP3**, **M4A/AAC → Digital · iTunes · AAC** (the store
is recorded in the copy's edition), and a folder that holds a concert disc (`.mka` /
`.mkv` / `.cue` and no separate tracks, e.g. an Atmos Blu-ray) becomes a **Blu-ray**
copy. Folders with no audio at all — artwork-only leftovers (`Folder.jpg`,
`desktop.ini`, `.db`) and QNAP housekeeping (`.@__thumb`, `@eaDir`, `#recycle`) — are
skipped. Re-running it adds nothing, so it is safe after every new rip:

```bash
python3 /app/music_import.py /data/media.db "/media/QNAP/Music"            # import
python3 /app/music_import.py /data/media.db "/media/QNAP/Music" --dry-run  # count only
python3 /app/tv_import.py /data/media.db "/media/QNAP/TV"                 # TV seasons
python3 /app/film_import.py /data/media.db "/media/QNAP/Mitchell's Movies" \
        --owner Mitchell                                                # ripped films
```

The media root is the Home Assistant media folder (`/media/QNAP`); everything below it
(`Music/...`, `Movies/...`, `TV/...`) is stored in the source path, which is exactly what
HA's `local` media source resolves. `location` on a copy is a shelf, so the importers
leave it empty - fill it in when you know where the disc actually lives.

Run it inside the running add-on with `docker exec app_local_media_collection …`.
Owner folders map to users by display name; a missing person is created. Without a
path argument it uses the first `media_roots` row with `medium = 'music'`, so the
entrypoint simply calls `music_import.py "$DB"` on every start and picks up whatever
has been ripped since the last boot (`music_path` in the options overrides the root
when the Supervisor passes it through).
`film_import.py` follows the same rules for a folder of films: one folder is one
film, `Name (Year) - Edition` is the title, and the file name decides the format
(`2160p` a 4K disc, `1080p` a Blu-ray, 3D a 3D Blu-ray, SD a DVD, otherwise
`--format`). Barcode, region and condition stay empty on purpose - those come from
a Blu-ray.com export. The start-up call covers `/media/QNAP/Mitchell's Movies` for
Mitchell; `FILMS_PATH` / `FILMS_OWNER` override it, and `--dry-run` prints what it
would add.


## Browsing

The catalogue page opens in one **mode at a time** — **Movies**, **TV** or **Music**
(an *Other* pill appears only if such titles exist). Under it, a second row of pills
lists **Everyone** and every household member who owns a copy (even with nothing in
the current mode, where the count shows 0), each with their copy count inside the
current mode; other people's private copies never appear, whichever filter is active.
Within a mode the page shows the title/copy counts with a per-format breakdown (CD,
3D Blu-ray, Blu-ray, 4K Blu-ray, Ripped file, …) as tick boxes — tick several to see
"Blu-ray **or** 4K" at once, and the count beside each box is what that format holds.
Tick boxes stack on top of the search and owner filters, and a poster keeps showing
every format its title holds even while the shelf is narrowed to one of them.
The search box and the list/poster toggle sit under the counts, and **+ Add a copy** sits
above them: it opens a form for a new title in the mode you are in. For music that form
also takes the artist and, if you have them, the artist's and the release's MusicBrainz
ids — enough to file an album without waiting for a later lookup. The **admin** link sits
beside the page title.

The poster view draws each title inside a **virtual case** picked from the best copy
held — a clear CD jewel case with its left hinge, a vinyl sleeve (the cover edge to edge,
no drawn frame), a blue Blu-ray case,
the same blue with a "Blu-ray 3D" band for 3D, and a black case lettered
"4K ULTRA HD" — so square album art is never cropped into a poster shape and the
shelf presence is visible at a glance. The case is two rows, band first and
artwork second, so the printed band (and its "Blu-ray" / "DVD" lettering) is always pinned
to the top edge whatever the browser does with the image. It is CSS only: no case
artwork to host.

Cover art comes from Jellyfin when the title is linked (`/art/<item>` proxies
Jellyfin's primary image and caches it under `/data/art`, so the page never refetches
it), and falls back to the `poster_url` the metadata refresh stored. Wikipedia only
carried a poster for a quarter of the films; Jellyfin has one for everything it
indexed, films and albums alike.

## If an upgrade fails

Each migration runs in one transaction, so a failure leaves nothing half-applied.
Before migrating, the add-on runs `repair.py`, which deletes scratch tables left by
an interrupted rebuild (`*_new`), recreates `digital_sources` if an earlier attempt
removed it, and drops views that no longer validate — the states that otherwise
make every later start fail. It also keeps `/data/media.db.bak`, the copy taken just
before the migration.

If the container is crash-looping after an update, check the **Log** tab: the first
line prints the running version (`media collection 0.8.x starting`). If it shows the
old number, the image was not rebuilt — use ⋮ → **Rebuild**, not Restart.

## Importing a Blu-ray.com export

**Import a CSV** (link on the catalogue page):

1. **Upload** the CSV (or paste it) — it is stored verbatim, nothing is inserted yet.
2. **Confirm the mapping** — every field (title, year, format, type, barcode,
   region, Blu-ray.com ID, edition, location, condition, IMDb ID, digital provider)
   gets a dropdown, pre-filled by matching your header names. Change any that are wrong.
3. **Review** — every row is staged with a status:
   - `ok` — ready to import (barcode may be empty; that is normal)
   - `duplicate` — barcode already owned, or repeated inside the file
   - `error` — missing title, year is not 4 digits, or barcode not 12/13 digits
4. **Commit** — only `ok` rows are inserted; failed inserts are reported per row.
   Imports are single-use: the page shows when it was committed.

Duplicates are matched on barcode only, so a second copy of a barcodeless title
imports as a new copy — remove it afterwards if it was accidental.

## Title detail page

Click a title in the list. The page shows the poster, year, runtime, genres,
certification, IMDb/TMDB links and plot; every copy you own with an inline editor
(format, edition, barcode, region, location, condition, visibility, **last
watched**); the digital sources; and **Refresh metadata** plus a **Watched today**
button per copy.

**Cover art** sits under the poster: every image the lookups have offered for the
title (Wikipedia, Wikidata's IMDb/TMDB hand-off, MusicBrainz covers) plus the
Jellyfin still, each a thumbnail you can click to make it the one the shelf shows.
The choice is stored in `titles.art_source` (`jellyfin` or `poster`), so a refresh
no longer overwrites a poster you picked, and the urls themselves are kept in
`titles.art_candidates` (JSON) so you can switch back later.

Formats are stored as `bluray`, `bluray_3d` (*3D Blu-ray*) and `uhd_4k`
(*4K Blu-ray*). The importer derives them from the export's disc counts and from
the `3D` / `4K` suffix Blu-ray.com appends to those titles; anything else can be
corrected in the copy editor.

**Refresh metadata** is keyless and IMDb-first: if the title has an IMDb id it
resolves that id on Wikidata (`haswbstatement:P345=…`) and reads the matching
Wikipedia page, so a corrected match sticks. Without an id it searches by name,
ignoring people and companies ("film director", "film studio") so a title like
*Akira* no longer lands on Akira Kurosawa. Saving a changed IMDb id clears the old
plot/poster and re-fetches immediately; clearing the id removes the metadata and
leaves a "Search IMDb for …" link.

## Media folders and Jellyfin

A copy is a *thing you own*; a playback source is *where its file lives*. The
catalogue fills that gap itself:

* **Scan the media folders** (Admin) walks what is under the media roots and links
  titles that have a copy but no file yet. Films and shows are matched by folder
  name and year, albums by `Music/owner/artist/album`, so punctuation, subtitles and
  `Name, The` sort names (`10,000 B.C.` vs `10 000 bc`, `Four Seasons, The` vs
  `The Four Seasons`) do not hide anything. A folder only counts when it actually
  holds a film or track, and pressing the button also moves a copy whose folder was
  renamed on the share. The same scan runs once when the add-on starts; it reads a
  handful of folders, not the whole share.
* **Path inside the root** on a title page is pre-filled with the folder the scan
  found, so adding a missing copy is a single click rather than a typed path.
* **Jellyfin**: paste the server URL and an API key (Dashboard → API Keys) under
  Admin → Jellyfin, press *Sync with Jellyfin*, and every copy is linked to the item
  Jellyfin already holds. The sync also runs once at start-up and whenever you press
  the button, so films added since the last one get their button without a visit to
  Admin; names are matched loosely (`10,000 B.C.` finds *10,000 BC*, and a catalogue
  subtitle may be dropped). The title page then offers *Play in Jellyfin*, which sends
  the film or album straight to a connected client. The key is stored in the
  database, so it survives upgrades; `jellyfin_url` / `jellyfin_api_key` in the
  add-on options still win when they are set.

## Updating a running install

`/data/media.db` is created once — later versions of the bundled catalogue never
overwrite it. On every start the add-on now:

1. **merges** any bundled rows your library is missing (discs match on barcode,
   barcodeless copies on title + year + owner), and
2. **normalises** titles that carry the format in their name: copies of
   `… 3D` / `… 4K` / `… Ultra HD` are flagged **3D Blu-ray** / **4K Blu-ray** and
   the suffix is stripped from the title (folding into an existing title of the
   same name and year if there is one).

Both steps are idempotent and log a one-line summary (`merge: +N copies`,
`normalise: N copies flagged …`). The catalogue page still offers a **Merge them**
button if you'd rather trigger the merge by hand.

A third step, **dedupe**, then folds titles that describe the same film: the disc
import has no year while the iTunes import does, so "Avengers: Endgame" could exist
twice. Titles are only merged when their names match and the years cannot
contradict each other (equal, or one unknown) — two different films with the same
name stay apart. Admin → Maintenance shows how many duplicates are left and offers
**Merge duplicate titles**.

Updating the add-on means **Update/Rebuild** in the add-on page (the Supervisor
bakes the image), not just a restart — a restart re-runs the same image.

## Filling in years in bulk

The disc export carries no film year, so those years start empty (the iTunes
import brought its own). Backfill from a shell when convenient:

    python3 media_collection/app/enrich_cli.py media.db        # local checkout
    python3 /app/enrich_cli.py /data/media.db                  # inside the add-on

One Wikidata lookup per title with a 0.5 s pause. Wikimedia throttles bursts with
HTTP 429; if you see many misses, wait a few minutes and run it again — it only
fills blanks, so re-running is safe.

## Options

| Option | Values | Default |
|---|---|---|
| `default_visibility` | `shared` / `private` | `shared` |
| `log_level` | `debug`/`info`/`warning`/`error` | `info` |

## Sharing

Every copy has one owner; `visibility = shared` (default) is visible to every
active HA user, `private` only to its owner. Ingress passes the HA user id, so the
add-on auto-creates an owner row per family member — no separate logins.

## Install (local add-on)

Copy this folder to `/addons/media_collection`, then Settings → Add-ons → Add-on
store → ⋮ → **Reload**, and install **Media Collection** under *Local add-ons*.
Bump `version` in `config.yaml` for each change — the Supervisor only rebuilds
when it changes. For a GitHub repo instead, replace `CHANGEME` in
`repository.yaml`, `config.yaml` and `build.yaml`.

## Data, backup, schema changes

`/data/media.db` (SQLite, WAL) is included in add-on snapshots. `migrate.sh` runs
on each start and records applied files in `schema_migrations`; add
`migrations/0002_*.sql`-style files, never edit an applied migration.

Foreign keys are per-connection in SQLite and default **off** — the app and
`migrate.sh` both enable them. Deleting an owner with copies aborts
(`trg_users_block_delete_with_items`); deleting a title with copies is blocked by
FK `RESTRICT`.

## Local development

```sh
./media_collection/migrate.sh /tmp/media.db
DB_PATH=/tmp/media.db python3 media_collection/app/server.py     # :8099
```

Requests without ingress headers map to a single `local` profile.

## Roadmap

1. ~~Import, review, search~~ (done)
2. Home Assistant "play this" routing per `playback_targets`
3. Jellyfin/Kodi item matching via `digital_sources.external_id`
4. Apple TV: switch device and open the app; selecting the exact purchase stays manual
