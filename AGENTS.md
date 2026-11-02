# Working notes for this repo (agent memory)

Home Assistant add-on: a household film/TV/music catalogue. Python 3 stdlib
only (`http.server`, `sqlite3`), no framework, no ORM, no tests directory.
Entry point `media_collection/app/server.py`; schema in
`media_collection/migrations/*.sql` applied by `migrate.sh`.

Version lives in `media_collection/config.yaml` and is displayed on the page
header. Current: **0.23.23**.

## Live deployment

- Host: HA OS `192.168.234.3`, app container `app_local_media_collection`.
- App inside the container: `/app`; database `/data/media.db`; listens on `8099`.
- From the dev Mac: `/tmp/h.exp '<command>'` opens the host shell (password on
  stdin is already wired). `sudo docker exec app_local_media_collection ...`.
- Repo checked out at `/Users/matt.hamilton/Developer/Media Collection`.

Bump → package → deploy:

```
# 1. bump the version and confirm it landed in the tarball
python3 -c "import io,re;c='media_collection/config.yaml';t=io.open(c).read();
t=re.sub(r'(?m)^version:.*$','version: \"0.23.N\"',t,count=1);io.open(c,'w').write(t)"
# 2. tar, upload, extract, update, verify - all of it
tools/deploy.sh
```

`tools/deploy.sh` (added after macOS wiped `/tmp` and took the old
`h.exp`/`apply.sh` helpers with it) tars `media_collection`, streams it to
`/tmp/mc.tgz` on the host, untars into `/addons`, runs `ha store reload` and
`ha apps update local_media_collection`, and exits non-zero unless
`version_latest` equals the packaged version.

- SSH key auth to `hassio@192.168.234.3` works directly; no expect script,
  no password on stdin.
- `scp`/sftp is refused by the SSH add-on ("Connection closed") - stream with
  `ssh host 'cat > /tmp/x' < file` instead.
- `sudo` needs no password on that shell. `ha` needs
  `export SUPERVISOR_TOKEN=$(cat /run/s6/container_environment/HASSIO_TOKEN)`.
- The version bump is still manual and must happen **before** packaging; three
  deploys were silently skipped because the tarball predated the bump. The
  script now fails loudly instead.

## Reading the code

Tool results come back as `[Content collapsed]` once they exceed roughly
300-400 characters. A 5-line `read_files` works, a 20-line one does not, and
commands batched into one call collapse together. Reliable tactics:

- `read_files` with **one** range of at most ~8 lines, one call at a time;
  or `sed -n 'Np' file | cut -c1-56` for a single line.
- `grep -c PATTERN file` to count, `grep -n PATTERN file | cut -c1-46 | head -2`
  to locate. Both fit.
- To hand a region to the user: `sed -n '308,404p' app/ui_title.py | pbcopy`
  then ask them to paste it. This ended a run of six failed blind edits.
- Anything that cannot be read back is patched by a script that asserts on the
  exact text it replaces (`assert s.count(old) == 1`) and prints one short
  line. That is how 0.23.8 and 0.23.9 landed safely.

## Data model essentials

- `items` — one row per **copy** of a title. `format` (`bluray`, `cd`, … and
  `ripped`), `rip_path` (a real file on disk, else NULL), `edition`,
  `owner_id`, `visibility`, `notes`.
- `digital_sources` — how a copy is reached: `provider` (`jellyfin`, `itunes`),
  `server_id`, `external_id`, `notes` (mirrors the server label).
- `jellyfin_servers` — `label`, `url`, `api_key`. The one registered server is
  labelled **Goodwood** (renamed from "Jellyfin"; 973 route rows updated).
- `jellyfin_links`, `playback_targets`, `users`, `settings`,
  `v_digital_sources` (view used by the UI for play sources).
- No `files` table. `_files(digital)` / `_stores(digital)` build dicts from
  the sources view.

## Invariants — do not break these

1. **A Jellyfin copy is stored as `format='ripped'` plus a `digital_sources`
   row.** Therefore any test of "is this a file?" must check `provider` /
   `rip_path`, never `format` alone. Getting this wrong offered a Jellyfin
   copy to a media player and Home Assistant failed to play it (fixed 0.23.9).
2. **Play gating**: the *From Media folder* block renders only when
   `playable = [s for s in playable if s["provider"] != "jellyfin" and
   (s["path"] or "").strip()]` is non-empty. There are currently **no**
   file-backed copies in the database (providers: `itunes` 167,
   `jellyfin` 973), so the block is correctly hidden everywhere.
3. **Copies order** (`_copies_tab`): disc copies → `+ Add a copy` button →
   ripped copies. `_row_html(item)` renders one copy; call it for

## Where things are in `app/ui_title.py`

Line numbers drift; use the search terms.

- `_copy_title(item, labels, store=None)` — the one-line label for a copy
  ("Ripped file", or format · owner, plus the store).
- `_stores(digital)`, `_files(digital)`, `_ripped(digital)` — dicts built from
  the sources view; the only reliable way to tell a route from a file.
- `_play_block(base, row, items, digital, players, last_player, jellyfin_link,
  jellyfin_configured, sessions…)` — the Play sheet: *From Media folder*
  (gated, see invariant 2) and *On the Jellyfin server*.
- `_copies_tab(base, row, items, digital, formats, labels, users,
  viewer_id=None)` — the Copies panel; `_is_file` + `_row_html` + the
  add-copy dialog.
- `_title_form`, `_art_block`, `render` — the edit form and the page shell.

## Known gaps (next work, roughly in order)

1. **Edit a Jellyfin server** (rotated API key, changed URL). Row template at
   `app/server.py` ≈ line 1044 (`'<li>%s <span class="muted">%s</span>…'`),
   insert handler at ≈ line 1315 (`INSERT INTO jellyfin_servers …`). Needs
   `POST /jellyfin/servers/<id>` updating `label`, `url`, `api_key`, plus an
   Edit control per row. Nothing currently allows changing a key without SQL.
2. **Edition label on the copy line** — one place: `_row_html` in
   `_copies_tab`, append ` · {edition}` when `item["edition"]` is set. Blocked
   only on harvesting trustworthy values (invariant 5).
3. **Multiple versions inside one Jellyfin item** (Almost Famous: one item,
   two media sources). Needs `digital_sources.media_source_id`, listing
   `MediaSources` during sync, and passing the id through `jellyfin.play()`.
   Item-level editions are already handled by `editions()`.
4. **Guard `POST /title/<id>/play`** server-side: it still accepts a
   `source_id` pointing at a Jellyfin route if the form is forged. The UI no
   longer offers it; the endpoint should refuse it.
5. **Mojibake in Jellyfin device names** — `Mattâ€™s MacBook Air`: UTF-8 decoded
   as latin-1 somewhere in the sessions list.

## Regressions this file has already caused (read before editing)

- Missing `server_id` in a `digital_sources` INSERT → every route broken.
- Format demoted on re-sync → disc copy turned into a "ripped" copy.
- A "blocked re-create" guard → titles could not be re-added.
- Six attempts at the copies order that all edited the wrong loop, and one
  that removed the `+ Add a copy` button entirely (the page kept showing only
  the dialog's heading). Cause: editing a 500-line file whose contents could
  not be read back. Rule: **if you cannot read the region, stop and get the
  text into the conversation** (`pbcopy`, or ask for a paste) before editing.

## Verifying a change on the live box

```
sudo docker exec app_local_media_collection sqlite3 /data/media.db "…"   # data
sudo docker logs app_local_media_collection 2>&1 | grep -m1 "starting"
sudo docker exec app_local_media_collection python3 /tmp/check.py        # page
```

Page checks are written as small scripts uploaded the same way the tarball
is (`/tmp/serve/*.py`, fetched with `curl` into the container). Render a page
with `urllib.request`, strip tags, and assert on the visible strings — that
is how "no copies yet", the copies order, and the play gating were verified.

   `not _is_file` then `_is_file`, with the button between the two loops.
   The button must be emitted *inside* `_copies_tab`; it was previously in a
   different function, which is why reordering the list did nothing.
4. **`editions()` in `app/jellyfin.py`** attaches further editions of a title
   (33 added). Match `have == want` or `have.startswith(want + " (")` /
   `want + " - ")`. A bare word-prefix match wrongly bound *Scream* ← *Scream 7*
   and *Despicable Me 2* ← *…3 Mini-Movie Collection*.
5. **`items.edition`** is currently empty everywhere on purpose: the
   name-only harvest produced junk ("Blu-ray", album subtitles) and was
   cleared. Harvest only with the **year from the Jellyfin record**, which is
   a separate field from the item name.
6. Migration inserts must list `digital_sources.server_id`; an early 0.22.x
   regression dropped it and broke every route.
7. Never block a re-create: `clean_job.py` and friends must not refuse a copy
   because a route already exists.

## Jellyfin versions (0.23.13)

One Jellyfin item can hold several files. Almost Famous (`6d88204a…`) holds
three: `Extended Edition (2000) - 2160p Dolby Vision, HDR10 DTSHD-MA`,
`(2000) - 2160p Dolby Vision, …` and `(2000) - 1080p TrueHD`. Library-wide 20
of 658 movies are multi-source, and *two* Jellyfin items exist for Almost
Famous (`6d88204a…` from 2026-06-09 and `e35b59…` from 2025-03-30) pointing at
the same three files in a different order — a Jellyfin-side duplicate, not
two copies on disk.

- `migrations/0019_jellyfin_versions.sql` adds `digital_sources.media_source_id`
  and `media_source_name`.
- `expand_versions(con, server, log)` at the end of `app/jellyfin.py` runs
  after `materialise()` in the sync loop. For every jellyfin route it lists
  the item's `MediaSources`, stamps the existing route with the first source,
  and inserts one further `ripped` copy (item + route, cloned from the first)
  per extra source. Idempotent: a route already carrying that
  `media_source_id` is skipped, so re-running creates nothing (`made: 0`).
- `items.edition` is set from the source name — "Extended Edition", or the
  quality when the source is unnamed ("2160p Dolby Vision", "1080p TrueHD").
  This is trustworthy data (Jellyfin states it), unlike the name-only harvest.
- `_row_html` in `_copies_tab` shows the version in the copy's detail line:
  `Jellyfin · Goodwood · 2160p Dolby Vision · 6d88204a…`.
- `jellyfin.play(con, session_id, jf_id, media_source_id=None)` now sends
  `MediaSourceIds`, and `POST /title/<id>/jellyfin` reads
  `digital_sources.media_source_id` for the posted `source_id`.

The Play sheet lists the versions: `_version_picker(digital, items)` in
`ui_title.py` renders a `Version` select of the title's jellyfin routes,
labelled with each copy's `items.edition`, and `_play_block` posts the chosen
`digital_sources.id` beside the device select. With no jellyfin route it
posts an empty `source_id`, and the handler falls back to `link_for_title`.

The sheet carries no empty sections: *From media folder* renders only when a
playable file exists (`shelf = ""` otherwise), and a title with nothing at all
returns before the `Play` button, so neither the button nor the dialog is
emitted. The `%s` placeholders in the Jellyfin form are positional and the
picker sits *after* `</select></label>` of the device select — swapping the
arguments nests one `<select>` inside the other and the browser then renders
every `<option>` as plain text.

The grid's play mark is `has_rip` in the main-page title query
(`server.py`, the sub-select just above `counts`), which used to mean
"provider = 'rip'" and therefore vanished for every Jellyfin title. It now
matches `IN ('rip', 'jellyfin')` and reads "a copy is ready to play" — 431 of
638 cards on the live home page, the rest being titles with no route at all.

### Gotchas found the hard way

- Jellyfin 10.9 removed `GET /Items/<id>` (HTTP 400 "Error processing
  request"); use `GET /Items?Ids=<id>&Fields=MediaSources&Limit=1`.
- `ha apps update` only rebuilds when `config.yaml`'s version changed. A
  deploy with the same number silently leaves the old `/app` running, so
  bump **every** time and check `version_latest`.
- A side-process write to `/data/media.db` fails with "database is locked";
  pass `timeout=60` and `PRAGMA busy_timeout = 60000`.
- `docker exec` wipes `/tmp` on every restart — re-upload helper scripts.

## Jellyfin duplicates across two libraries (0.23.19)

The household has two Jellyfin libraries (`Movies`, `Mitchell's Movies`) and
Jellyfin reports **one item per library** for a film it finds in both. The
catalogue therefore meets the same film twice, and also meets *different*
films that share a name — 15 titles were linked to two Jellyfin items:

- 6: the same film twice (either the same `MediaSources` paths, as with
  Almost Famous, or two files of the same year).
- 9: two different films on one title row — *Beauty and the Beast* 1991 with
  a 2017 route, *Conan the Barbarian* 1906 with 2011/1982 routes.

`dedupe_versions(con, server, log)` runs after `expand_versions()` in the sync
loop and decides by **film year**: keep the item whose `ProductionYear` is
within a year of `titles.release_year`, delete the other items' routes. If no
item matches the title's year, nothing is touched — a title whose year is NULL
or wrong must not lose copies. `_drop_copy()` always removes the route (a sync
rebuilds a correct one) and only removes the copy itself when it is synthetic
(`format='ripped'`, no `rip_path`) and holds nothing else.

Two items that report the **same** year collapse even when the title has no
year at all — that is one release held in two libraries (it cleared five music
titles: Pink Floyd 1975/1975, George Ezra 2014/2014, …).

Live result: with the title years filled in, the catalogue settles at **one**
doubled title (`Ed Sheeran — …`, four items spanning 2014/2021/2023 — music
whose releases legitimately differ and whose title has no year) and 980
jellyfin routes, stable across repeated syncs.

Order matters: `dedupe_versions()` must run **after** `editions()`, which
re-attaches the other library's item by name and puts back ~29 routes every
sync. Placed before it, the dedupe is undone immediately and the doubling
returns; placed after, each sync ends clean at the same 980 routes even though
the 29 routes are created and dropped again on the way.

Trap: do **not** dedupe on path equality alone. `_media_sources()` returns
`[]` for ids the API key cannot read, and two empty sets compare equal, so
four titles looked like duplicates when they were simply unreadable.
- Swapping two `%s` arguments in the Play form put the version `<select>`
  inside the device `<select>`; the page then showed the versions as bare
  text with no control. Positional `%` formatting bites here repeatedly.
