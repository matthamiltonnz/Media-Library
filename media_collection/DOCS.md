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

The catalogue page only carries a small **Library admin** link. That page holds the
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
  Wikimedia rate-limits bursts with HTTP 429: the job then **pauses** and the button
  becomes *Resume refresh* — press it later and it continues where it stopped,
  since it only targets titles still missing metadata.

The catalogue opens in **Thumbnails** view (poster grid, one card per title with its
formats and copy count); the **List view** link next to the search box switches to
the table. The choice is carried through searches.

"Add a copy" takes an optional **IMDb ID**; tick *Look up metadata* and the new
title is created with its plot/poster/year straight away, landing you on its
detail page.

## Playback sources (rips on the NAS)

Each copy can record where it can be played from: open a title → **Playback
sources**. Pick the copy, the provider (*Ripped file (NAS)*, Jellyfin, Kodi, iTunes,
Apple TV) and the **NAS root** plus the path inside it.

Roots are rows, not config — Admin → **Media roots** lists, adds and removes them,
so a new folder (or a moved NAS) is an edit, not a rebuild. Seeded with:

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

### Bulk import of a ripped music tree

`app/music_import.py` walks `<root>/<Owner's Music>/<Artist>/<Album>` and creates one
music title per album (named `Artist — Album`, year lifted from a `(1999)` suffix),
one `cd` copy owned by the person whose folder it sits in, and a `rip` playback
source pointing at the album folder. Re-running it adds nothing, so it is safe after
every new rip:

```bash
python3 /app/music_import.py /data/media.db "/media/QNAP/Music"            # import
python3 /app/music_import.py /data/media.db "/media/QNAP/Music" --dry-run  # count only
```

Run it inside the running add-on with `docker exec app_local_media_collection …`.
Owner folders map to users by display name; a missing person is created.

## Browsing

The catalogue page opens with a count of **titles and copies** plus a per-format
breakdown (CD, 3D Blu-ray, Blu-ray, 4K Blu-ray, Digital, …), so the headline is no
longer a bare copy count. A **media type** selector next to the search box filters
the whole page — and the counts — to Films, TV, Music or Other. The **Library admin**
link sits at the foot of the page.

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
