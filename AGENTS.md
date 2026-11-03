# Working notes for this repo (agent memory)

Home Assistant add-on: a household film/TV/music catalogue. Python 3 stdlib
only (`http.server`, `sqlite3`), no framework, no ORM, no tests directory.
Entry point `media_collection/app/server.py`; schema in
`media_collection/migrations/*.sql` applied by `migrate.sh`.

Version lives in `media_collection/config.yaml` and is displayed on the page
header. Current: **0.23.99**.

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
`ha apps update local_media_collection`, and exits non-zero unless the
**installed** version equals the packaged one.

It now refuses a version the box already runs, and polls `version` rather than
`version_latest`. Both were needed: on the 0.23.75 deploy `ha apps update`
queued the job, `version_latest` flipped to 0.23.75, the script printed
`deployed`, and the container was still running 0.23.74 for a minute - new code
apparently missing, `grep -c 'summary>Edit' /app/ui_admin.py` returning 0.
Check `ha apps info` for `version` (installed) vs `version_latest` (store); a
`sudo docker inspect -f '{{.Config.Image}}'` shows which build is live.

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

## Music: artist is its own field (0.23.26)

`titles.artist_id` → `artists(id, name)` has existed for a while and every one
of the 545 music titles carries it, but `titles.name` still held
"Artist — Album" (`artists.SEPARATOR = " — "`), so the page showed the artist
twice and the form had no way to change it.

- `migrations/0020_music_album_name.sql` strips the "<artist> — " prefix from
  music names (544 rows; each was verified to start with its artist's name).
- `_facts()` appends `row["artist_name"]` when the title query provides it, and
  the title page now selects `t.*, a.name AS artist_name … LEFT JOIN artists`.
- `_title_form` has an `Artist` input. Until 0.23.36 it did nothing: the
  `/title/<id>` POST updates name/year/kind/mbid directly and never called
  `artists.link()` — that only happened in `add_item()`, the create path. The
  edit branch now links the posted artist too, when `kind == "music"`.
- The artist page carries a **Rename artist** form (`POST /artist/<id>`, handled
  at the top of `do_POST` → `artists.rename()`), so a band that changed its name
  (Shihad → Pacifier for a US tour) is renamed in one place instead of per
  album. Typing a new name in a title's Artist box instead creates a *second*
  artist row and relinks just that title.
- `artists.link_all()` only touches titles with `artist_id IS NULL`, so the
  split names are not re-derived or lost.
- Jellyfin album routes are matched by **directory path** (`jellyfin.py` ~182,
  `by_dir[...]`), not by title name, so renaming titles does not unlink music.

Side effect worth knowing: with generic album names now matching on their own
("IGOR", "Good"), three music titles hold two Jellyfin items each — albums by
*different* artists that share a name. They cannot be told apart by year
(music titles have none). The fix, not yet done, is to require the artist to
agree — the artist name appears in the source path
(`…/Tyler, The Creator/IGOR/…`), so `dedupe_versions` could compare it against
`titles.artist_id`'s artist before keeping a route.

### Two deployment traps hit here

- `UPDATE titles SET … FROM artists a WHERE a.id = titles.artist_id` fails with
  "ambiguous column name: name" even when every reference is qualified as
  `titles.name`: SQLite resolves `SET name` against both tables. Use a
  correlated subquery with no FROM clause instead.
- A migration error leaves the add-on **Exited (1)** and `ha apps update` keeps
  reporting the new `version_latest`; the container has to be started again
  (`ha apps start local_media_collection` with `SUPERVISOR_TOKEN`) once the
  migration is fixed.

## Searching by artist (0.23.28)

- `/artists` takes `?q=` and filters the act list by name in the route
  (`server.py`, the artist-list branch: `all_artists()` then a case-insensitive
  Python filter), so the count/`view` toggle need no SQL change.
  `render_list(base, artists, view, query)` renders the search box above the
  list and carries `q` in the view-toggle link; its **empty** early-return
  branch also renders `search`, otherwise a search with no hits lost the box.
- The catalogue's `q` now matches the artist too: `visible_clause()` adds
  `OR EXISTS (SELECT 1 FROM artists ar WHERE ar.id = t.artist_id AND ar.name
  LIKE ?)` and a third `%query%` parameter. `visible_clause` params are
  positional and appended in statement order — the extras that callers add
  come after, so keep the three query params together when editing this.
- The grid card's caption under a poster reads `year · artist · formats`
  (`card["artist_name"]`, from a correlated subquery on `t.artist_id` added to
  the card query). `filter(None, …)` drops it for films, which have no artist.
- Net effect: `/artists?q=sheeran` → one act; `/?q=sheeran&kind=music` → three
  albums; `/?q=zzzz&kind=music` → none.

## Refreshing an album's metadata (0.23.31)

`refresh_music()` used to search MusicBrainz by `artist + album` derived from
`titles.name`. With the artist split out, the name is the album alone, so the
search looked for `-` and reported "no MusicBrainz match".

- It now looks the album up by its own `titles.musicbrainz_id` first, through
  `musicbrainz_release(mbid)`. The stored id can be a **release** or a
  **release-group**; the release-group endpoint 404s for a release id, so both
  are tried and the cover comes from the release group's id.
- Without an mbid it searches by name, taking the artist from `artist_id`
  (`artists.name`) instead of from the `"Artist — Album"` name.
- `musicbrainz_lookup()` asked the release-group index for
  `release:"…" AND artist:"…"`, which matches **nothing** — the field is
  `releasegroup:` and `artist:` does not work there either. It now sends
  `releasegroup:"<album>" AND arid:<artist mbid>` (the artist's id from
  `artists.musicbrainz_id`, 256 of 262 artists have one), falling back to
  `artistname:"<artist>"` and then to the album alone, and still gives `+30` to
  a result whose artist credit contains the artist.

Worth knowing: `arid:` turns a muddy search into an exact one — the album
alone returned two release groups and the score picked a 2001 reissue of
`Too Legit To Quit`, while `arid:<MC Hammer>` returns one hit, 1991-10-27.
A refresh always prefers a stored `musicbrainz_id`, so clear that field first
if it holds the wrong release.

## Filters survive the trip into a title (0.23.34)

The title page's *Back to the catalogue* link resolves its `?query` in three
steps, in `title_page()`:

1. `?from=<query>` on the title URL, if present;
2. otherwise the browser's `Referer`, unless it points at another `/title`;
3. otherwise the **`mc_back` cookie** the catalogue response now sets
   (`Set-Cookie: mc_back=<url-encoded query>; Path=<base>; SameSite=Lax`,
   via the `extra=` argument of `respond()`).

The cookie is what makes the filters survive a **POST**: saving the title form
redirects to `/title/<id>` with no query and no useful referer, and the browser
sends the cookie with that redirect, so the Back link still carries the view
the viewer came from. `title_page()` takes no `back` argument — it computes it;
passing one raises `TypeError` and the connection simply drops.

## Why a title had no Jellyfin copy (0.23.38)

`sync_links()` wrote `jellyfin_links` **without `server_id`**, while
`materialise()` selects its rows with `WHERE l.server_id = ?`. Result: the sync
reported thousands of matches and created no copies at all, and a title could
sit there with only its disc copy while Jellyfin plainly held the album. The
insert now carries `server["id"]` — the same missing-FK regression as the
0.22.x `digital_sources` one, in the other table. Check
`SELECT count(*), count(server_id) FROM jellyfin_links` first when copies
stop appearing.

Second, distinct cause, same symptom: `_name_match()` splits an album title on
`" — "` and looks for `(album, artist, album)` before falling back to
`(album, "", name)`. A title still named `"Pacifier — Pacifier"` matched
neither key, because Jellyfin credits the album to **Shihad** while the shelf
said Pacifier. With the name reduced to the album alone (`"Pacifier"`), the
name-only key hits and the route appears on the next sync.

## Fixing a Jellyfin match by hand (0.23.45)

Not every shelf title exists in Jellyfin, and not every Jellyfin item is on
the shelf, so the automatic match is not always right.

- The copies panel ends with a **Jellyfin matches** list built from the
  `digital` rows: matched item name, `jf_kind`, `jf_path` and the external id,
  each with an **Unlink** button (`POST /title/<id>/unlink`). Unlink deletes the
  `digital_sources` row, its `jellyfin_links` row, and the copy itself when it
  was a synthetic Jellyfin copy with nothing else on it. The route details come
  from `LEFT JOIN jellyfin_links l ON l.item_id = d.item_id AND l.jf_id =
  d.external_id` added to the title page's `digital` query.
- `/title/<id>/link` is a search page: `jellyfin.search_items()` wraps
  `/Items?searchTerm=…`, `jellyfin.describe()` gives name/kind/path for one id,
  and a **Link** button posts `POST /title/<id>/link` with `jf_id` alone.

A Jellyfin route hangs off a **copy**, not a title (`digital_sources.item_id`,
`jellyfin_links.item_id`), and a Jellyfin copy is a copy of its own —
`jellyfin.link_item()` builds one exactly as `materialise()` does (format
`ripped`, owner `rips`, visibility shared) and refuses an item that is already
linked. The first version of the page offered a list of the title's existing
copies instead, which would have bolted a Jellyfin route onto a disc copy and
said "this disc is also on Jellyfin" — two different ideas in one row.

`jellyfin_links.matched_by` is `CHECK (matched_by IN (' path', 'name'))` — no
'manual' value exists, so hand-picked links are recorded as `'name'`.
- Both handlers sit at the top of `do_POST`, next to the artist rename, so they
  run before the generic routing.

Gotcha that cost several cycles: a `do_GET` branch must call
`self.respond(code, page)` itself. Returning the HTML string instead sends
**nothing** — curl reports "Empty reply from server" (status `000`) and the
container log shows no traceback, because no exception was raised. Debugging
prints inside the builder proved it was reached and returned ~10 KB, which is
what pointed at the missing `respond`.

Still open: browsing *media folders* (only Jellyfin search is offered), and the
report that the catalogue's media filter is reset (to music) when coming back
from a title.

## Playing a Jellyfin copy crashed (0.23.46)

Symptom: pressing **Play** on a Jellyfin copy killed the request thread with
`TypeError: execute expected at most 2 arguments, got 3` at the
`owner_item = con.execute(...)` line of the play handler, and the client saw a
dropped connection.

Cause: a patch had inserted a line into the handler's SQL and left a **comma**
after the first fragment, so Python passed it as a separate argument:

```python
con.execute(
    "SELECT item_id, media_source_id",          # <- stray comma
    " FROM digital_sources WHERE id = ?",
    (int(chosen),))
```

Implicit string concatenation only happens while the literals are adjacent —
one comma and the statement becomes three arguments. When editing SQL split
over several lines, always check the **trailing character** of each line.

A quick way to exercise the whole play path without a real client: post
`/title/<id>/jellyfin` with `source_id=<a route id>` and
`session_id=no-such-session`. Success looks like a 200 page carrying
`Jellyfin: POST /Sessions/no-such-session/Playing - …`, i.e. Jellyfin's own
rejection surfaced as a banner; a dropped connection means our side raised.

## `database is locked` was a dropped page (0.23.47)

`connect()` opened sqlite with `timeout=10` and no `busy_timeout`. With WAL and
a background sync writing, ten seconds was occasionally not enough, and the
failure surfaced as `sqlite3.OperationalError: database is locked` inside
`ensure_user` — which kills the request thread, so the browser saw the
connection close with **no response at all** (curl status `000`). That is the
same "empty reply" shape as a missing `self.respond(...)`, which is why it sent
me looking in the wrong place twice. Both are now 60 s.

## The startup dedupe was skipped every boot (0.23.49)

`run.sh` ends with `python3 dedupe.py "$DB" || echo "dedupe step skipped"`, so a
failure there is quiet. It was failing with
`UNIQUE constraint failed: digital_sources.item_id, digital_sources.provider,
digital_sources.external_id`.

`digital_sources` is `UNIQUE (item_id, provider, external_id)`, and `copies()`
folded duplicate copies with a blanket
`UPDATE digital_sources SET item_id = ? WHERE item_id = ?`. Once two copies
held routes to the same Jellyfin item — which the version expansion makes
easy — that UPDATE collided. It now moves the sources one at a time and
deletes the row when the keeper already reaches the same
`(provider, external_id)`.

It also connected with `sqlite3.connect(path)` and no timeout, so it died with
`database is locked` whenever the app was writing; both connections now use
60 s. Live run:

```
dedupe: 0 mixed copy(s) split
dedupe: 6 duplicate copy(s) folded
```

A copy of the database from before the first real run is at
`/data/media.before-dedupe.db` inside the container.

## "Greatest Hits" all pointed at one album (0.23.51)

`_index()` registered two keys per album: `("album", artist, name)` and a bare
`("album", "", name)`. The bare key let *every* title called "Greatest Hits"
fall onto whichever album was registered first — Creed's — because
`setdefault()` keeps the first claim. Fixed two ways:

- the bare key is recorded as **ambiguous** (`None`) as soon as a second album
  claims it, so nothing matches on it;
- `_name_match(index, name, year, kind, artist="")` now takes the title's
  artist (`titles.artist_id` → `artists.name`, selected in `sync_links`' query)
  and tries `("album", artist, album)` before any fallback.

Check for leftover bad matches with:

```sql
select count(*) from digital_sources ds
  join items i on i.id = ds.item_id join titles t on t.id = i.title_id
  left join artists a on a.id = t.artist_id
  left join jellyfin_links l on l.jf_id = ds.external_id
 where ds.provider = 'jellyfin' and t.kind = 'music'
   and l.jf_name is not null and a.name is not null
   and lower(l.jf_name) not like lower(a.name) || '%';
```

Live: 20 rows out of 976, and every one is a spelling variant of the same act
(`Apollo 440` / "Apollo Four Forty", `St Germain` / "St. Germain",
`The Prodigy` / "Prodigy", curly vs straight apostrophe in `Guns N’ Roses`) —
or a genuine collaboration (King Gizzard with Mild High Club).

The copies panel shows a Jellyfin copy **once**. `_row_html()` returns an empty
string for it and the **Jellyfin matches** list is the presentation — name,
kind, path and id, with Unlink. "Link a Jellyfin item by hand" sits *below*
that list. A second line on the copy row saying the same thing was noise.

Match details are looked up by `jf_id` (a subquery), not by `item_id`: a dedupe
fold can move a route onto another copy, so the link row need not hang off the
same item.

## The scan button without media folders (0.23.55)

Admin offered **Scan the media folders** even when `media_roots` was empty,
where the job can only fail. `_scan_block(base, message, roots)` now replaces
the button with "No media folders are set up yet …" when `render()` passes
`len(roots) == 0`, and `POST /admin/scan` bounces back to `/admin` instead of
starting a job that has nothing to walk.

## Progress on the admin page (0.23.57)

The three background jobs used to bounce you to a banner at the top of the page.
`ui_admin.render(..., jobs=...)` now receives their state — the admin route
passes `{"scan": scan.status(), "artists": artists.status(), "jellyfin":
jellyfin.progress()}` — and shows a live line under each block, plus a
`setTimeout(location.reload, 4000)` that only arms while something is *running*.

`jellyfin.PROGRESS` is a module dict updated inside `sync_links()` (phase
"reading the library" → "matching copies" → "done", with `done`/`total` every
100 copies). `start_sync()` works on a **thread in the same process**, so those
counters are visible to the web handler; a subprocess would not be.

The artist fill now counts: `enrich()` sets `_STATE` to
`running/done/total/filled` and the admin line reads
"Filling artist details - 9 of 186 acts, 7 filled." Its form posts and then
redirects to `admin#artists`, and `_artists_block` carries `id="artists"`, so
clicking it no longer throws you to the top of the page (a plain 303 to
`/admin` does exactly that — the browser starts a fresh page at the top).

Still to do: `scan.py`'s `STATE` has no `files` counter, so its line reads
"Scanning the media folders…" with no number.

The artist fill's counts live in `artists.summary()`, so they appear inside the
Artists block: "Filling artist details - 11 of 48 acts, 1 filled." A separate
line appended by `render()` was useless — it landed above the scan block, next
to the wrong section, which is why the numbers never appeared where you looked.


## One refresh area on the admin page (0.23.65)

Both background lookups sit under the **Metadata** heading in
`ui_admin._refresh_block` - *Refresh all metadata* (Wikipedia/Wikidata for
films and TV, MusicBrainz for albums) and *Fill artist details*. The Artists
block keeps its `id="artists"` and its `artists.summary()` line but has no
button any more; pressing either job from one place is the point, and the
status still reads where the act list is.

The artists form posts to `action="admin/artists"` - **relative**, unlike the
`%sadmin/refresh` beside it, so it resolves correctly whether the app is served
at `/` or under a prefix. Removing that form also removed `_artists_block`'s
only use of `base`, so its `% (state, html.escape(base))` had to lose the second
argument; matching that tuple by text failed (assert count 0 - the indentation
differs from what was guessed), so the patch locates `html.escape(base))` with
`index()` after the `% (` and cuts there. A one-element `% (value,)` tuple is
valid, so the trailing comma left behind is harmless.

The metadata copy now says what music actually gets: an album gains a
MusicBrainz release, year and cover, never a plot, because MusicBrainz has none.
Nothing in `refresh_job.py` or `metadata.py` calls Jellyfin; a Jellyfin poster is
only used on demand by `ui.pick_cover`. So this button cannot recover artwork
that exists only on the server - that would be a separate feature.

Verified live: `Fill artist details` appears once on `/admin`, the artists block
is heading plus summary (102 chars, no form), and the forms are
`['/admin/refresh', 'admin/artists', ...]`.

## Album cards and the artists page (0.23.68)

An album card under an artist used to read "1999 - CD, Ripped file". Two
problems: "Ripped file" is how a Jellyfin route is spelled in `items.format`,
not news worth printing on 545 cards; and the artist list had no owner filter,
unlike the catalogue.

- `_formats(row, labels)` in `ui_artists.py` drops `ripped` from the format list
  and appends a play glyph when the row has one. Both the grid caption and the
  list view use it. "Ripped" here means "playable", which is what `has_rip`
  tests on the catalogue grid (provider `rip` or `jellyfin`) - not the "is this
  a file?" question of invariant 1, so testing `format` is right in this place.
- The glyph is a bare U+25B6, not `<span class="playmark">`: the caption is
  wrapped in `_text()` (which escapes), so a span arrived on the page as
  `&lt;span class=&quot;playmark&quot;`. Use the span only where the markup is
  not escaped - the catalogue grid builds its own HTML.
- `artists.all_artists()` gained `owner_ids` (a `group_concat(DISTINCT
  i4.owner_id)` subquery), `render_list()` takes `owners`/`chosen` and renders
  the same `nav.modes.owners` chips the catalogue uses, and the `/artists`
  branch counts artists per owner in Python and filters on `?owner=`. Live:
  Ripped files 242, Jeanine 13, Mitchell 12; `?owner=5` -> 13 artists with the
  chip marked current.

Trap hit here: `patch144.py` died on an `assert` in its second step, so its
third step never ran - but the first step had already written `artists.py`. The
container was then rebuilt with a new `artists.py` and a **stale** `server.py`,
and `/artists` rendered 257 artists with no chips. `grep -c owner_ids /app/*.py`
showed `server.py:0`; a local grep for `owner_rows` had looked right (3 hits)
because the catalogue's own owner chips use that same name. When a patch script
touches several files, verify **each** file on the box.

## Filters survive the artists page too, and the scan counts (0.23.69)

Two loose ends from 0.23.68.

- `render_list()`'s **view toggle** now carries `owner` as well as `q`
  (`/artists?view=list&amp;owner=5&amp;q=dido`), so switching between the grid
  and the table no longer drops you back to every act. The search box gets a
  hidden `name="owner"` input inserted by string surgery on the finished form
  (`search.replace("</form>", ...)`) when a filter is active - a GET form
  **replaces** its action's query string with the submitted fields, so the owner
  cannot ride on `action="%sartists?owner=N"`. The little *clear* link beside the
  box still drops `owner` on purpose: it is there to widen the view again.
- `scan.STATE` gained `files` and `folders`, counted in `_listdir()` (a
  module-level `counted` set keeps a cached listing from being counted twice,
  and `start()` clears it). `summary()` reads "Scanning the media folders -
  1,234 files in 56 folders so far." while a scan runs, so the admin line is no
  longer a numberless "Scanning...". Verified by setting `scan.STATE` by hand
  rather than by walking the real shares.

Still open: artist `image_url` is empty for every act (needs Fanart.tv or
Wikipedia portraits), and "Link a Jellyfin item by hand" offers search only -
there is no way to browse the server's media folders from the page.

## A refused link now says why (0.23.70)

Linking a Jellyfin item by hand that was already linked appeared to do nothing:
`jellyfin.link_item()` returns **None** when a `digital_sources` row already
holds that `(server_id, external_id)`, and `POST /title/<id>/link` ignored the
return value. Its `except` handler was worse - it printed to the container log
and redirected as though the link had worked. So the page reloaded unchanged,
with no copy and no explanation.

- `link_item` still returns None rather than raising (it is a query-shaped
  outcome, not a failure), but the route now checks: on None it looks up the
  title holding the route (`digital_sources` -> `items` -> `titles` by
  `external_id`) and redirects to `/title/<id>/link?error=...` with
  `Not linked: <jf_id> is already linked to "<title>" (#<id>). Unlink it there
  first.` Any exception is surfaced the same way, with its own text.
- `link_page()` gained `notice=""`, merged into the `note` paragraph the page
  already used for search counts, and the GET side reads `?error=`.

Verified live: posting an already-linked `external_id` returns `303` to
`/title/1/link?error=Not linked: 0045bff2... is already linked to "The Lion
King" (#222)...`, and that page renders the sentence.

Worth knowing about the model: linking three Jellyfin items to one vinyl title
(Minis Mixes vol. 1-3 on a 3-sided record) creates three separate `ripped`
copies, one per item - there is no "side" concept tying them to the vinyl.
Each link is independent, so all three can be done from the same link page.

Still unexplained: *why* an item that no `digital_sources` row mentions was
refused. The swallowed exception was hiding it; the new message will show the
real text on the next attempt.

## Deleting the last real copy takes its shadows with it (0.23.71)

Deleting an incorrect CD left the title behind, holding nothing but the
Jellyfin copy that had been linked to it. `POST /items/<id>/delete` removed the
copy and its `digital_sources` rows, then deleted the **title** only when *no*
items remained - and the `ripped` Jellyfin copy counted as an item, so the title
survived as a phantom with one unplayable route on it.

The route now also deletes the item's `jellyfin_links` row (that was dangling
before), then asks whether any **real** copy is left on the title:

```
SELECT COUNT(*) FROM items WHERE title_id = ?
   AND (format IS NULL OR format <> 'ripped'
        OR (rip_path IS NOT NULL AND TRIM(rip_path) <> ''))
```

When that is 0, every remaining copy on the title is dropped (sources, links,
item) and the existing "a title with no copies left is not a title" statement
then removes the title row. `format <> 'ripped'` alone would be wrong - a real
ripped *file* has `rip_path` set and must count as real (invariant 1).

Verified on a copy of the live database (`/tmp/deltest.db` in the container,
never the real one): a title with one CD + one shadow went from `(1 item, 1
route)` to nothing at all, while a title with two real copies kept its shadow
after one was deleted (`(3,2) -> (2,2)`, swept 0).

Deliberately **not** done: a bulk sweep of existing phantom titles. Many titles
legitimately hold only Jellyfin routes (`itunes` 167, `jellyfin` 973 routes, no
file-backed copies at all), so "no real copy" does not mean "should not exist".
The orphan the user was left with has to be Unlinked by hand.

## Why a hand-picked album link always failed (0.23.72)

The link page said `CHECK constraint failed: jf_kind IN ('movie', 'album',
'season', 'series', 'episode')`. `jellyfin.describe()` returns Jellyfin's own
`Type` lowercased - `musicalbum` - and `link_item()` wrote that straight into
`jellyfin_links.jf_kind`, so **every** manual album link died on the CHECK. The
sync never hit it because `_index()` labels albums `"album"` itself. The old
`except: print(...)` in the route swallowed the IntegrityError, which is why the
first report looked like "nothing happens".

`link_item()` now maps the type to the words the table knows (`musicalbum`,
`audio`, `musicvideo` -> `album`; `boxset` -> `movie`; series/season/episode as
themselves) and falls back to the title's own kind, so an unknown type cannot
break the insert. Verified end to end on a copy of the database: search finds
`mini mix vol. 1` as `MusicAlbum`, and `link_item` stored `album` and created
the copy.

Note the vocabulary in the data: `jellyfin_links.jf_kind` holds only `album`
(1028) and `movie` (1019) - there is no `audio` value despite `_index()` having
a counter for it.

## Delete a title outright (0.23.72)

`POST /title/<id>/delete` removes every copy on the title (sources, links, item)
and then the title itself, and the title page carries a **Delete this title**
button with a confirm. This is the way out of a title left behind by a bad copy
without Unlinking each Jellyfin match by hand.

Verified on the live box with a throwaway title: the button rendered, the POST
answered 303, and `titles`/`items` came back 0 and 0.

## The admin page is one panel per job (0.23.73)

The page used to be flat headings with two of them reading *Media folders*, and
the Jellyfin section came twice: `_jellyfin_block` drew the old single
`jellyfin_url` / `jellyfin_api_key` form while `server.py` injected a second
*Jellyfin servers* panel by string surgery (`page.replace("<h2>Media
folders</h2>", panel + ...)`), so the servers list, the clean button and the
folders table were piled on top of one another.

- `ui_admin._panel(title, body)` wraps a block in `<section class="panel">`
  (bordered, rounded, 62rem max). `render()` emits Jellyfin, Media folders,
  Metadata (with the `Artists` summary as `h3 id="artists"`), Players (only when
  Home Assistant reports any), Import and export, Maintenance. Every heading on
  the page is unique.
- `render()` grew two arguments: `jobs["clean"]`, so the clean job's progress and
  auto-reload travel the same path as the others, and `confirm`, the HTML from
  the new `ui_admin.confirm_root()`. **`server.py` no longer edits the rendered
  page** - there is no `page.replace(...)` left in it. Anything added to the
  admin page is passed in, never spliced.
- The Jellyfin panel lists `jellyfin_servers` (label, address, whether a key is
  set, Remove) and always ends with the **Add a server** form. The sync button
  appears only when at least one active server exists. `jellyfin.status()` now
  carries `"servers"` (every row, `is_active` included) and `"routes"`
  (`digital_sources` rows with `provider = 'jellyfin'`) for this. Print `routes`,
  never `linked`: `jellyfin_links` holds one row per item per library, so on the
  live box it reads 2047 while the catalogue has 981 playable Jellyfin copies.
- The add form posts to `%sjellyfin/server` - **singular**. The handler tests
  `.endswith("/jellyfin/server")`, so a plural `/jellyfin/servers` falls through
  to a 404 with nothing in the log. Delete is `%sjellyfin/server/<id>/delete`.
- `.panel` rules live in `ui.py` beside the rest (`.panel table`, `.panel > h2`,
  `.panel > h3`, `.panel form`); `h3` had no styling before this.

Checked on a scratch DB (`sh media_collection/migrate.sh /tmp/test.db`) with the
server on :8123: `/admin` 200; headings `Jellyfin, Media folders, Metadata,
(Artists), Import and export, Maintenance`; `?confirm_root=6` opens the dialog;
POST `/jellyfin/server` -> 303 and the row appears with *Sync with Jellyfin*;
POST `/jellyfin/server/1/delete` -> 303 and the panel falls back to *No server
yet*.

## Editing a server, and a quiet artists line (0.23.75)

- Every server row is followed by a second `<tr class="edit">` holding a
  `<details><summary>Edit <label></summary>` form that posts to
  `%sjellyfin/server/<id>` (no `/delete`). The handler sits just after the
  delete branch and matches `r"/jellyfin/server/(\d+)$"` on
  `route.rstrip("/")`; the add handler's `.endswith("/jellyfin/server")` cannot
  catch it, and the delete suffix cannot match the `$` anchor.
- The API key is **never rendered** (`type="password"`, empty value,
  `placeholder="leave blank to keep the current key"`), so an edit sends `""`
  and the handler keeps `row["api_key"]`. Verified: posting an empty key left
  the stored key alone, posting `rotated` replaced it, and the page HTML
  contained the old key nowhere.
- `config(con)` - what `play()`, `sessions()`, `image()` and `search_items()`
  use - **used to read only env, options and the legacy `settings` pair**, so
  editing `jellyfin_servers` would have changed nothing for playback. It now
  prefers the lowest-numbered active `jellyfin_servers` row and falls back to
  `settings`, and the edit handler mirrors the primary row into
  `settings.jellyfin_url` / `jellyfin_api_key` so the two never disagree
  (checked: `config(con)` returned the edited URL and key).
- The `Artists` line ("Filled 0 artist(s) from MusicBrainz on ...") is now
  emitted **only while the artists job is running** -
  `render()` adds `_artists_block(base)` when `jobs["artists"]["running"]`, so
  an idle page has no stale count and no `id="artists"` anchor. The anchor
  therefore exists exactly when the fill does, which is when the
  `admin#artists` redirect lands. `artists.summary()` itself is unchanged.

## The add forms hide behind their own button (0.23.76)

`Add a server` and `Add a root` are `<details class="add"><summary>Add a
server</summary>` blocks - collapsed until clicked, no JavaScript. The summary is
styled as a button (`.panel details.add > summary`: border, radius, `list-style:
none` plus a hidden `::-webkit-details-marker`, a `+`/`-` prefix) so it does not
read like the bare `Edit <label>` summaries above them, and they are **never**
auto-opened - not even when the list is empty, which is why the roots empty
state says "press *Add a root* above" rather than "add one below".

## The add-title dialog is gated by media type (0.23.77)

`server.py`'s `PAGE` template (the catalogue) carried an *Add a title* dialog
whose fields all sat in one `form.add` grid, with the **Media type** select
buried in the middle and `onchange="applyKind(this.value)"` on it - a function
that only exists in `ui.SHELL` (where it drives the title page's *Add a copy*
dialog), because `PAGE` had **no `<script>` at all**. So on the catalogue that
call was a silent JavaScript error and nothing was ever gated.

- The dialog is now two fieldsets - *The title* (Media type first, then Title,
  Year, IMDb id for film/TV, Artist + two MusicBrainz ids for music) and
  *The copy* (Format, Owner, Store, Visibility, Edition, Barcode, Where it
  lives) - with real `<label>` text above each input instead of
  placeholder-only fields, and the metadata checkbox + Cancel/Add in an
  `.actions` row.
- Gating is the `hidden` attribute on `label.music` / `label.screen`, driven by
  `addKind()` in a new `<script>` at the end of `PAGE`. `[hidden] { display:
  none !important; }` is needed because `form.add .grid label { display: grid }`
  otherwise beats the user agent's `[hidden]` rule. The three music labels also
  carry `hidden` in the markup, so the default (Film) is right before the script
  runs.
- `addFormat()` upgrades the media type to music when vinyl/cd/sacd/cassette is
  chosen - one way only, so a digital album is never flipped back to a film.
- `form.add` is no longer the grid; each fieldset holds its own `.grid`. The old
  `fieldset.music` rules are gone, replaced by `form.add fieldset` / `legend`
  (with `min-width: 0`, or a fieldset's `min-content` blows the dialog out on a
  phone).
- Trap: `PAGE` is a `string.Template`, so any `$` added to the markup or the
  script becomes a substitution placeholder. Only `$base`, `$format_options`,
  `$owner_options` and `$store_options` exist there.
- Verified on a scratch DB: two legends, music fields hidden by default, and
  POST `/items` still creates a film (imdb + metadata) and an album (artist
  linked).
- Worth knowing: posting an `imdb_id` forces a metadata refresh in the POST
  handler (`wants_metadata = bool(fetch_metadata) or bool(imdb_id)`), so the
  year typed into the form is replaced by the id's year - tt0111161 turned 1999
  into 1994 during testing. Deliberate ("an id means look it up"), not a
  regression from the dialog.

The barcode scanner and lookup landed in 0.23.78 - see the next section.

## Scanning a barcode, and what it looks up (0.23.78)

The add dialog's Barcode row now carries **Scan** and **Look up**.

- `startScan()` opens `<dialog id="scanner">` (a `<video playsinline muted>`)
  and reads frames with `BarcodeDetector` (`ean_13`, `ean_8`, `upc_a`, `upc_e`,
  `code_128`) every 400 ms. On a hit it stops the stream, puts the digits into
  `input[name="upc_ean"]` and runs the lookup. Only Chromium-based browsers
  (Chrome/Edge, Android included) have `BarcodeDetector`; Safari does not, so
  the dialog says so plainly and the field stays there for typing. `getUserMedia`
  needs HTTPS or localhost - HA ingress is HTTPS, a plain `http://host:8099` is
  not, and the camera refuses there.
- `lookUp()` calls `GET <base>barcode?upc=<digits>` - relative, so it follows
  `<base href>` under an ingress path - and fills **only empty fields**, so a
  second scan never overwrites what was typed. A MusicBrainz hit also flips the
  media type to music and fills the artist and release id.
- `GET /barcode` (in `do_GET`, beside `/health`) answers
  `{"upc", "found", "note", "existing"}`; 400 for anything that is not 12 or 13
  digits. `existing` names the title when a copy already carries that barcode
  (shared copies, or the viewer's own).
- `metadata.upc_lookup(upc)` tries **MusicBrainz** `release?query=barcode:<upc>`
  first and falls back to **UPCitemdb**'s keyless trial endpoint for anything
  that is not an album. The note carries whatever went wrong ("MusicBrainz is
  rate-limiting us...", "UPCitemdb has no product for that code."), so the page
  can say why it found nothing instead of staying silent.
- Two guards the live data forced: the MusicBrainz search is **fuzzy**, so a
  candidate only counts when `release["barcode"]` really equals the scan (a
  `barcode:0000000000000` search cheerfully returns six releases), and codes
  whose digits are all the same are rejected as placeholders - without both, a
  junk barcode resolved to "Alessandro Alessandroni Plays His Music From
  Spaghetti Westerns".
- Verified: `upc_lookup('7293627293259')` -> *Jazz Beatles* (MusicBrainz, 2000);
  `0000000000000` -> the placeholder note; `123456789012` -> the album **and**
  `existing: {"id": 1, "name": "Dialog Test Film"}` for the copy that carries
  it; 3 digits -> 400. The JS was run in node against a stub DOM: it fills
  title/year/artist/mbid, flips the kind to music, and leaves typed values
  alone on a second scan.

## Locations, and a store that is null (0.23.79)

- `migrations/0022_media_locations.sql` fills `items.location`, which was NULL
  on all 2283 copies. It works by **format**, not by title kind: vinyl (whoever
  owns it) -> *Lounge kallax*; Matt's bluray/3d/4k/dvd/vhs/other -> *Lounge
  shelf*; Matt's cd/sacd/cassette -> *Bedroom storage*; Mitchell's discs ->
  *Mitchell's bedroom*. Owners are found by `display_name`, never by id, so the
  file is safe to read on another install. `digital` (iTunes) and `ripped`
  (Jellyfin routes) stay NULL - they are not on a shelf - as do Jeanine's and
  Justin's copies, which nobody asked about.
  Live result: 504 *Bedroom storage*, 409 *Lounge shelf*, 187 *Mitchell's
  bedroom*, 15 *Lounge kallax*, 1168 still NULL.
- A music **title** held on a disc gets the disc's shelf: 8 of Matt's concert
  Blu-rays and DVD reissues sit in *Lounge shelf*, which is where the case is.
- The copy editor's **Store** box read "not a purchase" for everything. Two
  things were wrong: the empty option carried that claim, and `_store_options`
  compared the option *key* (`itunes`) against `chosen`, which `_stores()`
  hands over as the *label* ("Apple TV") - so even a bought copy showed "not a
  purchase". It now matches either, and the empty option is a plain `-`.
- The add dialog's Store list had **no** empty option, so it defaulted to Apple
  TV. `store_options` now starts with `<option value="">-</option>`, so a disc
  is saved with no store at all (`add_item` writes a `digital_sources` row only
  for `store in PURCHASE_STORES`, so nothing is recorded).
- Checked against a copy of the live database (`/tmp/live.db`, taken with
  `sqlite3 /data/media.db ".backup /tmp/live.db"` and `cat`-streamed out of the
  container): the migration applies as migration 0022, `/title/1` renders
  `name="location" value="Lounge shelf"`, an iTunes copy selects *Apple TV* and
  a plain disc selects `-`.

## A duplicate title is refused, a duplicate copy is questioned (0.23.80)

`add_item()` used to *reuse* any title with the same name and year and quietly
append the copy, so adding "The Lion King" from the catalogue gave you a second
copy of the existing row with no word about it.

- `add_item(con, viewer, form)` now returns **(error, title_id, clash)**.
  `clash` means nothing was written and the caller must show `clash_page()`:
  `{"kind": "title", "title": row}` for the catalogue dialog (a brand-new title
  that is already here), or `{"kind": "copy", "title": row, "format": fmt,
  "copies": [...]}` for the copy dialog (same owner, same format).
- The two dialogs are told apart by the **`from=title`** field the title page's
  *Add a copy* form already carried; the catalogue dialog posts no `from`.
- `/items` answers **409** for a refused title and **200** for the copy
  question. `clash_page()` renders either through `ui.page(...)`: the refusal is
  a `.panel` with a button to `/title/<id>?tab=copies`, the question is a
  `<dialog class="sheet" open>` that re-posts the whole form through
  `_hidden_fields(form, ("confirm_copy",))` plus `confirm_copy=1`.
- `_title_row()` returns the artist name too, and `_same_act()` keeps a music
  match honest: two bands can both have *Greatest Hits*, so a matching name
  under a different artist is a new title. Verified: same act -> 409, different
  act -> 303 and a new row, no artist at all -> 409 (it cannot tell, so it does
  not guess).
- `_same_copies()` matches on **title + owner + format**, never on the title
  alone: a second copy is a real thing (lounge and bedroom), so it asks and
  never refuses.
- The copy dialog lost its *Media type* select. It existed only to feed a
  hidden `kind` through `onchange="this.form.kind.value=this.value"`, and it
  always *displayed* Film, even on a music title. The hidden `kind` (the
  title's own kind) stays. That left `ui.SHELL`'s `applyKind()` and the two
  script blocks hunting for `select[name="kindpick"]` dead, so they are gone -
  `applyKind` had already been replaced by `addKind` in `PAGE`.
- The Barcode box gained `pattern="[0-9]{12,13}"`, so a mistyped code is caught
  by the browser instead of coming back as a 400 page that loses the form.
- Checked on the copy of the live database (`/tmp/live.db`): re-adding
  "10,000 B.C." (2008) -> 409 and the page names title #1; a blu-ray copy for
  Matt -> 200 "Matt Hamilton already owns a Blu-ray of 10,000 B.C. (2008) in
  Lounge shelf", `confirm_copy` -> 303 to `/title/1?tab=copies` and the copy
  count went 2 -> 3; a dvd or a Mitchell copy -> 303 with no question.

## The catalogue's filters survive the Back link (0.23.81)

Opening a title from a filtered catalogue and pressing *Back to the catalogue*
came back to a different view - the owner filter, and anything filtered after
it, was gone. The cause was **encoding**, not logic. The thumbnail card linked
to `title/<id>?from=<query>` with the query only HTML-escaped, so the first `&`
ended the `?from=` value:

```
/title/764?from=?q=dido&kind=music&owner=2
   parse_qs -> from="?q=dido", kind="music", owner="2"
```

The title page only reads `from`, so `back` became `?q=dido` and the other
parameters were dropped on the floor. `title_page()` takes the first `from`
that starts with `?` and never consults the Referer afterwards, so the
truncated value won and the browser's real URL was never asked.

- `render_index()` now builds `back_suffix = "?from=" + quote(here, safe="")`
  once, and both link builders use it: the grid card (`{5}`, template changed
  from `title/{1}?from={5}` to `title/{1}{5}`) and the list row
  (`ROW` gained `$from_query`). The list row carried **no** `from` at all
  before, so it leaned on the Referer; now both views are explicit.
- `title_page()` needed no change: `parse_qs` decodes the value back to
  `?q=dido&kind=music&owner=2`, which starts with "?" as it expects.
- Verified on a copy of the live database: `/?q=dido&kind=music&owner=2&format
  =cd&view=grid` cards link to
  `?from=%3Fq%3Ddido%26kind%3Dmusic%26owner%3D2%26view%3Dgrid%26format%3Dcd`
  and that title's Back link is
  `/?q=dido&kind=music&owner=2&view=grid&format=cd`; the list view does the
  same; `?owner=2` and `?owner=3` still return different card counts.
- The `mc_back` cookie was never the problem - it stores
  `quote(urlparse(self.path).query)`, the whole query, which is why the filters
  survived a **POST** but not a card click.
- `ui_title.render()`'s `back is None` fallback is what hid the bug from
  `/title/<id>` fetched on its own: it builds `?kind=<row kind>` (+ owner) so a
  title with no `from`, no Referer and no cookie still renders.

Still open: `ui_artists` links to `%stitle/%d` with no `from`, and the act list
sets no `mc_back`, so a title opened from `/artists?owner=5` comes back to the
catalogue rather than the act list. Fixing that needs the back target to carry
a **path**, not just a query, because `ui.page()` does `base + back`.



## Title page rework: the pencil, the biography, the pinned player (0.23.82)

### Where cover art actually lives

`titles.poster_url`, `titles.art_candidates` and `titles.art_source` (migration
0014). `art_candidates` is a JSON list of `{"source", "url"}` - every image a
lookup ever offered, capped at the last 12 by `metadata.remember_art()` - and
`poster_url` is the one in use when `art_source = 'poster'`. **No image files
are stored**: a url points at Wikipedia/IMDb or at `/art/<jf_id>`, which
proxies Jellyfin. So "deleting a cover" is editing those three columns, which is
what `POST /title/<id>/art` now does with `action=`:

- `use` (default) - the old behaviour, `source=jellyfin` or `source=poster&url=`;
- `forget` - drop that url from `art_candidates`, and if it is `poster_url`
  clear `poster_url`/`art_source`;
- `auto` - `art_source = NULL`, i.e. Jellyfin first then the poster, keeping
  the fetched poster as the fallback.

The chooser is a pencil on the bottom-right corner of the cover, inside a
`.coverwrap` that now *floats* the image (the old rule floated `img.cover`
itself, so the pencil would have floated away from it). Each candidate in the
sheet has a small x Forget button, and the sheet ends with an *Automatic* tile.

A title with no image at all (98 of 1182 on the live shelf) gets neither a grey
placeholder nor a pencil: `_art_block()` returns "" when there is nothing to
choose between, and `render()` only draws the `.coverwrap` when there is a
cover to decorate.


### The biography on an artist page

`artists.overview` was junk - MusicBrainz's disambiguation, so "Group" or
"American singer-songwriter and pianist" - because MusicBrainz has no
biography. `migrations/0023_artist_bio.sql` adds `wiki_url`, `link_urls`
(JSON) and `bio_at`, and `metadata.artist_bio(name, mbid)` fills them from
Wikipedia: the lead extract, the lead image, and links to Wikipedia, Wikidata
(from `prop=pageprops`), MusicBrainz and a Discogs search.

- `metadata.wikipedia_artist_name()` tries the plain title, then `(band)` /
  `(musician)`, then Wikipedia's own search - and every hit must pass
  `_about_music()` (a role word from `MUSIC_ROLES`) and must not be a
  disambiguation page, or a film called "Heat" becomes a band.
- `artists.fill_bio()` runs at the end of `enrich()` (so *Fill artist details*
  does it) and on its own from the artist page's **Fetch details** button
  (`POST /artist/<id>/bio`). An act whose lookup comes back empty is stamped
  with `bio_at` anyway, otherwise every run re-asks about the same handful of
  acts with no article; the button bypasses that.
- `render_artist()` shows the bio only when `wiki_url` is set, so rows still
  holding the old "Group" text show nothing until they are filled.
- `ui.page(..., back=...)` builds `base + back`, and `title_page()` now accepts
  a `from` that is a **page path** (`_back_target()`): the act page links with
  `from=artist/2?view=grid`, so Back returns to the act and reads
  "Back to the artist" (`ui.page()` grew `back_label`; SHELL's static wording
  became `@@BACKLABEL@@`). That closes the "a title opened from /artists comes
  back to the catalogue" gap.

### The pinned player

`ui.NOW_PLAYING` is one string - `<style>`, the `<aside id="nowplaying">`
markup and its script - dropped in by `ui.page()` through `@@NOWPLAYING@@` and
by the **catalogue's own `PAGE`** through `$nowplaying`. `PAGE` is a
`string.Template` and is *not* `ui.SHELL`; forgetting it left the bar on every
page except the home page, which is exactly where you start playback from.

- `GET /nowplaying` answers `{"playing": false}`, or the item:
  `jellyfin.now_playing()` reads `/Sessions` and prefers a playing session over
  a paused one, then the most recently active. `server.now_playing_json()` maps
  the item back through `digital_sources.external_id` - trying the item, album,
  parent and **series** id - so the bar uses our own cover and links to
  `/title/<id>`; an unmapped item falls back to `/art/<album id>` and no link.
- The script polls `fetch('nowplaying')` every 5 s. It is **relative** on
  purpose: both shells carry `<base href>`, while `$base`/`@@BASE@@` differ
  between them. `POST /nowplaying` takes `action=pause|resume|stop` +
  `session_id`, answers JSON (`jellyfin.control()`) and the bar hides itself,
  setting `body.has-nowplaying` (5.2rem bottom padding) so the last row of a
  page is still reachable.
- Trap: the pencil's `[data-open]` must work on a title with nothing to play,
  and `_play_block` returns early there - so its inline script is never
  emitted. The click handler lives in the shell now, guarded with
  `!sheet.open` because the play sheet's own handler is registered first.

### An album played one track

`jellyfin.play()` sent `ItemIds: <album id>`, and Jellyfin starts only the first
track of an album item. It now takes `kind` and, for music, sends the album's
tracks: `album_tracks()` lists `/Items?ParentId=<album>&IncludeItemTypes=Audio
&SortBy=ParentIndexNumber,IndexNumber` and `ItemIds` becomes a comma-joined
list. Verified against the live server: *Homogenic* -> 10 ids.

Also fixed while there: `MediaSourceIds` was sent as `[media_source_id]`, and
`urlencode()` turns a list into a Python repr (`%5B%27...%27%5D`) which Jellyfin
ignores. It is a plain string now.


## What 0.23.82 got wrong (0.23.84)

Three things the first cut of the rework shipped broken.

- **The pinned player never appeared.** `now_playing_json()` returned the
  session dict without `playing: True`, and the bar's first line of JavaScript
  is `if (!data || !data.playing) { hide; return; }` - so every poll looked
  like silence. The endpoint was answering correctly all along
  (`title`, `cover`, `subtitle`, `position` were all there); only the flag was
  missing. Any new field the bar keys off has to be checked in the *rendered*
  JSON, not just in Python.
- **The cover shrank to a thumbnail.** `img.cover` carries
  `width: 170px; max-width: 38%`, and wrapped in a floating `.coverwrap` the
  percentage resolved against a box whose width is shrink-to-fit *from the
  image* - so the picture collapsed. The wrapper now owns the size
  (`width: 170px; max-width: 38%`) and the image is `width: 100%;
  max-width: none; height: auto`.
- **The Jellyfin device names are all Jellyfin gives.** `/Sessions` carries
  `DeviceName` (whatever the app reported: "android TV", "Google Chromecast"),
  `Client` ("Moonfin for Android TV"), `ApplicationVersion`, `UserName`,
  `DeviceId` and `RemoteEndPoint`; `/Devices` adds `AppName`, `AppVersion`,
  `LastUserName`. There is no room, model or friendly name anywhere in the
  API - a custom name set in Jellyfin's own Devices page is the only way to get
  "Lounge TV", and it arrives as `DeviceName`. `_device_label()` therefore
  builds `"<device> (<client> <version>)"`, `sessions()` adds the user and
  sorts by `LastActivityDate` (newest first, so the device you just used is at
  the top of the picker).


## The Play button moved to the top (0.23.85)

`_play_block()` in `ui_title.py` returns **two strings** now - `(button,
sheet)` - instead of one. `render()` calls it right after the facts line and
appends only the button there; the sheet (a modal, so where its markup sits in
the document does not matter) is appended after the copies and the delete
block. The button used to sit below the copies, so starting a film meant
scrolling past every copy of it first.

Two things fell out of that change:

- the old `head` ("No copies recorded yet.") is gone - the Copies section says
  that itself, and at the top of the page it read as a remark about the wrong
  thing;
- the sheet no longer carries its own `[data-open]` script, because `ui.SHELL`
  has had the generic handler since 0.23.82. A title with nothing playable
  returns `("", "")`, so there is still no button and no dialog - the hint
  ("No file to play yet - rip the disc...") is now emitted in the button's
  place, above the copies.


## An album links to its act (0.23.86)

`_facts(row, base)` in `ui_title.py` takes `base` now and renders the artist as
`<a href="<base>artist/<id>">` when the row carries `artist_id`. It does: the
title page query selects `t.*` plus `a.name AS artist_name`, and `t.*` includes
`artist_id`.

- Everything on that line is escaped *inside* `_facts`, and the caller appends
  the result unescaped (`'<p class="sub">%s</p>' % facts`) - so only the artist
  bit may be markup. `_text()` on each label, an anchor for the artist.
- A film has no `artist_name`, and an album whose `artist_id` is NULL renders
  plain text, so nothing changes for either.
- The act page's album cards already carry `from=artist/<id>?view=...`, so
  album -> artist -> album -> Back lands on the artist again.


## The filter pills work on every page (0.23.87)

`nav.modes` - the pill row that filters by kind on the catalogue and by owner on
the artists page - was styled **only** in the catalogue's own `PAGE`. `ui.SHELL`
had no such rules, so `/artists` drew the identical markup as a run-on of
underlined links: "EveryoneRipped files242Matt Hamilton233...". The four rules
now sit in both shells, the same way `.cards` and `table` already do.

Two things in `render_list()` were wrong as well:

- the **Everyone** pill had no count, unlike the catalogue's. It takes a
  `total` now - the number of acts *before* the owner filter narrows the list -
  which the `/artists` branch passes as `len(rows)`.
- its `current` class was **inverted** (`"" if chosen is None else "current"`),
  so the unfiltered list marked Everyone current *and* marked the chosen owner
  when there was one. It reads `"current" if chosen is None else ""` now,
  exactly like the catalogue's `% ("" if owner else "current")`.


## No link twice on an artist page (0.23.88)

`render_artist()` printed MusicBrainz twice for every act that had a biography:
once in the info line beside the country ("All artists · MusicBrainz · GB ·
formed 1980") and again in *Read more*. The `link_urls` JSON is parsed at the
top of the function now, and the info line only offers MusicBrainz when that
row does not already carry it:

- a filled act -> `All artists · GB · formed 1980`, with MusicBrainz inside
  *Read more*;
- an act with an id but no biography yet -> the info line keeps the link,
  because there is no *Read more* row to hold it;
- an act with no id at all -> "Look up on **MusicBrainz**", which is the search
  page and a different link altogether.


## Standard transport buttons on the pinned player (0.23.89)

The bar had word buttons ("Pause"/"Resume") and a bare text square for stop,
which read as three different things. It now carries two round `.npbtn`s with
inline **SVG** glyphs - play/pause and stop - because the Unicode media
characters (`\u23f8`, `\u25b6`, `\u25a0`) render differently, or not at all,
from font to font and platform to platform.

- **One toggle, two faces**: both glyphs are in the DOM and
  `#nowplaying.paused .i-play` / `.i-pause` decide which shows. `paint()` sets
  that class from `data.paused` and writes the button's `title`/`aria-label` as
  "Play" or "Pause", so the label says what a press will *do*, not what it is.
- **Stop closes the box**: `press('stop')` calls `hide()` *before* the POST and
  sets `quietUntil = Date.now() + 4000`, which makes `poll()` return early -
  without that the next five-second poll paints the bar straight back while
  Jellyfin is still clearing the session, which looks like Stop did nothing.
- A refused control (`{"ok": false, "note": ...}` from `POST /nowplaying`) is
  written into the bar's subtitle now instead of being swallowed by the JS.

`jellyfin.control()` maps the three actions to
`/Sessions/<id>/Playing/{Pause,Unpause,Stop}`; all three return Jellyfin's 404
for an unknown session, and an unknown action is refused before any HTTP call.


## No "Ripped files" chip (0.23.90)

The artists page built its owner chips from a Python tally over every
`owner_ids` entry `all_artists()` returned, and that includes the synthetic
`rips` account the Jellyfin and file copies belong to - so "Ripped files 242"
sat beside the household's names, counting every playable title. The tally now
skips `users.username = 'rips'`, which is the rule the catalogue's owner query
already used (`WHERE u.username <> 'rips'` in `render_index`).

Nothing else moves: the copies still belong to that account, the chip is only
absent from the *filter*, `?owner=<rips id>` still filters if asked directly,
`/api/state` still reports it, and the format filters never offered `ripped` at
all (`name="format" value="ripped"` appears nowhere - only the add dialog's
format select has that option).


## Previous, next, rewind, fast-forward (0.23.91)

Jellyfin answers every playback command on **one** route -
`POST /Sessions/{sessionId}/Playing/{command}` - where the command is the
`PlaystateCommand` enum: `Stop, Pause, Unpause, NextTrack, PreviousTrack, Seek,
Rewind, FastForward, PlayPause`. That is not guesswork: Jellyfin 10.9 serves its
OpenAPI document at `/api-docs/openapi.json` (2.7 MB), so
`paths["/Sessions/{sessionId}/Playing/{command}"]["post"]["parameters"]` lists
the enum and the optional `seekPositionTicks`. `jellyfin.CONTROLS` gained `next`,
`previous`, `forward` and `rewind`; `seek` is deliberately absent because
nothing offers a seek bar.

`ui.NOW_PLAYING` carries six round buttons now - previous, rewind, play/pause,
fast-forward, next, stop - and previous/next are hidden unless the item is
music: `paint()` toggles `#nowplaying.music` from `data.kind === 'audio'`, and
`#nowplaying.music .npbtn.prev` / `.next` is what shows them. Rewind and
fast-forward stay for every kind. On a narrow screen the cover makes way
(`@media (max-width: 27rem)`) so six buttons still fit beside the title.

`press()` no longer drops a press while another is in flight. A counter
(`inFlight`) replaced the boolean `busy` flag, so tapping next three times sends
three commands while the five-second poller still keeps out of the way - with
the flag only the first of four quick taps went through, which running the live
script in node showed at once.


## Several players at once (0.23.92)

`jellyfin.now_playing()` used to pick one session and throw the rest away, so a
second player going in another room was invisible. It splits in two now:

- `active_sessions(con)` returns **every** session with a `NowPlayingItem`,
  sorted playing-before-paused and then by `LastActivityDate`. A phone casting
  to the TV makes two sessions, which is why the list - not a single pick - is
  the honest shape;
- `now_playing(con, session=None)` returns the asked-for session, else the
  first of that list.

`GET /nowplaying` accepts `?session=<id>`, and `now_playing_json()` answers with
the chosen item **plus** `sessions`: a short list of `{session, device, title,
paused}` for the bar. An id that has gone falls back to the best session and the
bar adopts `data.session`, so a player that stops never leaves the bar dead with
a stale session id.

`ui.NOW_PLAYING` draws a pill per player under the subtitle - only when there is
more than one - the current one in the same green as the filter chips
(`.np-players button.current`) and paused ones dimmed. Pressing a pill sets
`now.session` and polls `?session=` again, so pause, stop, next and rewind all
act on that player afterwards.

Trap: `now_playing_json()` calls `jellyfin.active_sessions(con)` **once** and
picks from that list. Calling `now_playing()` for the item and `active_sessions()`
for the pills would hit Jellyfin's `/Sessions` twice per poll, per open page.


## Correcting an act's MusicBrainz id (0.23.93)

The artist page carries a **MusicBrainz id** box under *Rename artist*: the fix
for a lookup that failed or matched the wrong act. `artists.set_mbid()` does
more than store a string.

- It validates the 36-character uuid first; a typo comes back as
  `?mbid_error=...` and **nothing is written**.
- It then re-reads `metadata.musicbrainz_artist_by_id()` and writes country,
  start year and kind from that record - the fields a bad lookup leaves blank or
  takes from the wrong act.
- It drops `link_urls` and `bio_at` (not the biography: that was looked up by
  *name*, so it is not invalidated by the id) - the "read more" row disappears
  until *Fetch details* rebuilds it against the id now stored.
- A blank box clears the id on purpose. That is why the form posts a hidden
  `action=mbid`: `parse_qs` drops blank values, so without it an empty submit
  would be indistinguishable from "this is the other form".

The POST handler is the existing rename branch in `server.py`: `artist_name`
present -> `artists.rename()`; `action=mbid` -> `artists.set_mbid()`. Both
redirect back to the act, the second with `?mbid=` or `?mbid_error=`, which
`render_artist(notice=..., bad=...)` shows as a `.banner good|bad`.


## An act's article comes from its MusicBrainz id (0.23.94)

`metadata.artist_bio()` looked acts up on Wikipedia by **name** only, so "Air"
resolved to the gas and the French band had no biography - and because
`fill_bio()` stamps `bio_at` on a failure, that act was never asked about again.

- `musicbrainz_url_rels(mbid)` reads MusicBrainz's own relationships
  (`/artist/<id>?inc=url-rels`): `wikidata`, `wikipedia`, `discogs`, `allmusic`,
  `last.fm`, `imdb`, `official homepage`, … It sleeps `MB_PAUSE` after the call
  like the other MusicBrainz readers - one request a second.
- `artist_bio()` now tries the id first: MusicBrainz `wikidata` rel -> qid ->
  `enwiki_title(qid)` -> the article. A `wikipedia` rel is the next fallback,
  and the name search runs only when the act has no id or MusicBrainz links it
  to nothing.
- The links row uses those rels instead of guessing: a real Discogs *artist*
  page plus AllMusic / Last.fm / IMDb / official site when they exist, capped at
  six. The Discogs *search* link survives only as the fallback.
- `_about_music()` grew the other disambiguation wordings: "Topics referred to by
  the same term" is how `Air (band)` introduces itself, and it was slipping
  through the name path.
- `migrations/0024_artist_bio_retry.sql` sets `bio_at = NULL` where `wiki_url` is
  NULL, releasing the 16 acts the old rule had stamped as hopeless - the job only
  asks about rows with no stamp, so without this they would stay empty forever.

Verified: `Air` -> `Air_(French_band)` (491 chars, portrait, six links), `Björk`
-> `Björk`, `St Germain` -> `St_Germain_(musician)`, `Apollo 440` ->
`Apollo_440`. `Moog 213` -> `Blair_Joscelyne`, which is right: Moog is that
person's channel.


## One refresh button, and it runs by itself (0.23.95)

The Metadata panel had two forms - *Refresh all metadata* (titles) and *Fill
artist details* (acts). One button starts both now: `POST /admin/refresh` calls
`refresh_job.start()` **and** `artists.start()`, and the panel says so.

What runs when:

- **Startup** (`server.py main()`): scan, the Jellyfin sync, then
  `artists.start(DB_PATH, limit=60)` and - new - `refresh_job.start(DB_PATH,
  limit=60)`. Both take a `limit`, because a shelf holds hundreds of titles no
  lookup will ever fill (190 music titles with no id/poster and 267 films/TV with
  no overview/poster on this one); an unbounded sweep on every restart would keep
  Wikipedia busy for nothing. In steady state both find nothing and cost a query.
- **Adding a title**: `add_item()` links the artist, then calls
  `artists.enrich_one(con, artist_id)` - the MusicBrainz record plus the
  Wikipedia biography for a new act, and a single row read for an act already on
  the shelf. The add dialog's *Look up metadata* box is ticked by default, so the
  title itself is fetched too.
- **The button**: everything missing, unbounded, with `force` to re-fetch titles
  that already have metadata.

`artists.enrich_one()` and the job share `_fill_from_musicbrainz(con, row)`, so
the two paths cannot drift apart.

Trap: `refresh_job.start()` used to have no `limit`, and `main()` calling it
unbounded meant every restart would re-walk the whole backlog - the same titles,
forever, because some can never be matched.

## Clean database left the media folders (0.23.96)

`_clean_block` - the **Clean database** button - sat at the bottom of the *Media
folders* panel, beside *Add a root* and *Scan the media folders*, though it is not
about the folders: it validates rows already recorded. It now sits in
*Maintenance*, under *Merge duplicate titles*.

What it actually does (`clean_job._run`), and what it does **not** touch:

1. `DELETE FROM digital_sources WHERE provider='jellyfin' AND (server_id IS NULL
   OR server_id NOT IN (SELECT id FROM jellyfin_servers))` - the **only** place a
   Jellyfin route can go, and it fires only when the server row itself has been
   removed. Its row count is kept in `STATE["routes"]`.
2. `provider='rip'` rows whose `root_id` is not a known root are deleted, then
   every remaining `provider='rip'` row with a path is `os.path.exists()`-ed
   against its root and the missing ones deleted.
3. `dedupe.orphans()` prunes copies left with nothing - `format='ripped'`, no
   barcode, notes not `rip pending`, and **no `digital_sources` row at all**. A
   Jellyfin copy always carries its route, so it and its title survive.

`itunes` rows and anything without a `provider='rip'` path are never checked -
there is no file to look for. The live shelf has **zero** `provider='rip'` rows
(providers: `itunes` 167, `jellyfin` 981), so pressing it today checks nothing,
drops nothing, and reports "no file-backed copies to check".

Verified on a copy of the live database: a ghost route (server 9999) seeded onto a
healthy Jellyfin copy, plus a second copy holding *only* such a route, then the
job - `jellyfin` routes 981 -> 981, items 2288 -> 2288, both ghost routes gone,
the ghost copy pruned, and the first copy kept with its live route and its title.
Three seeded `provider='rip'` rows (one real file, two missing) with a root gave
"3 of 3 files checked, 2 forgotten" and kept the real file.

The progress line reads `Clean: <files> checked, N forgotten, N Jellyfin routes
dropped, N copies pruned - done`, with "no file-backed copies to check" instead
of a file count when there are none - it used to say "0 of 0 checked" and
"1 copies pruned".


## Dead Jellyfin routes go too (0.23.97)

A Jellyfin copy whose item has been deleted from the server cannot play, and no
sync will rebuild it - `materialise()` only creates routes from items that exist.
Clean database asks each server and drops those routes.

- `jellyfin.existing_ids(con, server_id, ids, chunk=50)` - one `GET /Items` per 50
  ids (`Ids=a,b,c&Recursive=true&Fields=Path&Limit=50`), returning the set the
  server confirmed. It returns **None** when the question could not be put (no
  such row among `servers()`, which is `is_active = 1` only, or no url) and lets
  `JellyfinError` through for a request that really failed, so "could not ask" and
  "everything is gone" stay distinguishable. An empty `external_id` is never in
  the answer.
- `clean_job._drop_dead_routes(con)` groups routes by server, calls that once per
  server, and for every id the server did not return deletes the route and its
  `jellyfin_links` row (`item_id` + `jf_id`), leaving the copy to
  `dedupe.orphans()`. It runs **before** `dedupe.orphans()` in `_run`, after the
  dead-server and file checks.
- A server that raises `JellyfinError`, or that `servers()` does not list, is
  skipped whole and the page says so: "Goodwood could not be asked which items it
  still holds, so its copies were left alone." That guard is the important one -
  a dropped connection must never look like an empty library and wipe 981 copies.
- `clean_job.STATE` grew `verified` (routes confirmed alive), `skipped` (labels of
  servers not asked) and `phase` ("asking Jellyfin which items it still holds",
  shown while it runs). The note is two paragraphs now: `Clean: ... - done` and
  `Jellyfin: 981 copies checked against the server, 0 routes dropped.`

Measured against the live server on a copy of the live database: all 981 routes
(945 distinct ids - one item can back several copies) were confirmed present, so
nothing is dropped today. Two seeded ghost ids (`deadbeefdeadbeef` on a healthy
copy, `cafebabecafebabe` on a copy holding nothing else) gave `routes=2,
pruned=1`, jf 983 -> 981, items 2294 -> 2293, the healthy copy and its live route
intact, the ghost's `jellyfin_links` row gone, its copy pruned, its title kept.
Pointing the server url at `http://127.0.0.1:9` gave `routes=0, pruned=0,
skipped=['Goodwood']` and deleted nothing.

Trap: `_drop_dead_routes` runs **before** `dedupe.orphans()` on purpose. The
route is deleted first, so by the time the prune asks "does this copy hold
anything?" the answer is honest; run the prune first and every dead route's copy
survives, because the route still looks like a reason to exist. It also relies on
`jellyfin_links` being keyed by `item_id` - the table allows one link row per item
(`UNIQUE (item_id)`), so the delete is `item_id = ? AND jf_id = ?`.


## The "Include ripped files" switch is gone (0.23.98)

The catalogue hid titles whose every copy is a file (`RIP_ONLY`) and left those
copies out of the headline counts (`RIP_COPY`), with a link under the tallies -
*Include ripped files* - to lift both. It was there for the first library import,
which arrived as a wall of files, and it has been removed: every title is listed
and every copy counts. (The copy count went back to ignoring files in 0.23.100
- see *Files are not counted* at the end of this file.)

What went with it, in `render_index` / `library_rows` / `library_stats` /
`visible_clause` and the `PAGE` template: the `rips` query parameter, the `rips=`
argument through four signatures, both SQL fragments, the `$rips_toggle`
placeholder, the `rips_toggle=` substitution, the `("rips", 1 if rips else "")`
tally input, the `&amp;rips=1` carried by the view toggle **and** by the kind /
owner chip links, and the `rips or row["fmt"] != "ripped"` test on the format
pills. `?rips=1` is ignored now - it changes nothing.

Trap: the flag had a dozen homes and only one of them was the visible link. They
are not all in one function, and a missed one compiles fine and then raises
`NameError: name 'rips' is not defined` on the first page load - which is what the
first pass did, in `extra` (the filter string the kind and owner chips carry).
`grep -n '\brips\b' server.py` must come back with the three `username <>
'rips'` rows and nothing else before this is finished; the `ripped` owner account,
`dedupe.RIP_OWNER_NAME` and `ui_title.FILES_OWNER` are a different idea and stay.

The visible effect on the live shelf is nil: there are no `provider='rip'` rows
(981 `jellyfin`, 167 `itunes`), so both fragments matched nothing - the home page
reads 636 titles · 1220 copies either side of the change.


## The repository is a public add-on repository (0.23.99)

It already was, in structure: `repository.yaml` at the root and the add-on in
`media_collection/` is exactly what Settings -> Add-ons -> Add-on store ->
Repositories expects. What was missing was everything around it that a stranger
touches.

- `README.md` carries the two My Home Assistant badges -
  `supervisor_add_addon_repository?repository_url=…` (add the repository) and
  `supervisor_app?app=media_collection&repository_url=…` (open the add-on) - and
  the Install section now says what the machine needs: Home Assistant OS or
  Supervised (Container cannot install add-ons), amd64/aarch64/armv7/armhf/i386,
  internet for the first build, and that **no prebuilt image is published**, so
  the Supervisor builds on your own machine. `supervisor_addon` is a legacy slug
  that my.home-assistant.io maps to `supervisor_app` with `addon` -> `app`; both
  still answer, the new one is what the badge is called now.
- `media_collection/icon.png` (128x128) and `logo.png` (250x100) were missing, so
  the store and the dashboard showed a blank tile. `tools/make_icons.py` draws
  them - film strip, green play mark, dark plate - with 4x4 supersampling and a
  hand-written PNG writer, no library. Re-run it rather than editing the binary.
- `media_collection/translations/en.yaml` names and explains the two options, so
  the Configuration tab reads "Default visibility" instead of `default_visibility`.
  The Supervisor reads these from the add-on folder, not the image - the
  Dockerfile copies only `app/`, `config.yaml`, `migrations/`, `migrate.sh`,
  `run.sh`.
- `repository.yaml`'s maintainer was `<matt@example.com>`; it is the GitHub
  noreply address now, so the descriptor has no placeholder in it.
- `tools/check_addon_repo.py` (run by `.github/workflows/addon-repo.yml` on every
  push to `main` and every pull request) checks the descriptor keys, the config
  the Supervisor requires, that `build.yaml` builds every architecture `arch`
  offers and that each base image tag **really exists in ghcr.io**, the artwork
  sizes, and that the README still carries both install links pointing at
  `repository.yaml`'s url. It has its own tiny YAML reader so it needs no
  `pip install` - `python3 tools/check_addon_repo.py [--offline]`.

Trap hit writing this, and it is this file's own warning about truncated reads:
`head -6 build.yaml | cut -c1-50` printed `aarch64-base:3.2`, `3.2` is a 404 at
ghcr.io, and the line was edited to `:3.21` — but it **already said `3.21`**;
the `cut` had eaten the last character. The edit produced `:3.211`, which the new
checker flagged immediately (`aarch64-base:3.211 does not exist`). Nothing was
ever wrong with the ARM base image: the box is `x86_64`, so it had always built
from `amd64-base:3.21`. Read whole values before "fixing" them; truncating for
readability is only safe for text you are not about to edit.

Verified: checker exits 0 with all five base-image tags confirmed;
`deploy.sh` packaged and installed **0.23.99** (`installed: 0.23.99`), the
container came back `started` and `/health` answered `{"status": "ok"}`;
`/addons/media_collection` now holds `icon.png`, `logo.png` and `translations/`.
Both badge redirects answer 200, and `raw.githubusercontent.com/…/main/`
serves `repository.yaml` and `media_collection/config.yaml`, so the address in
the README is one Home Assistant can actually add.

Still open: nothing prebuilds an image, so every install compiles on the user's
box. Adding `image: ghcr.io/<owner>/media-collection-{arch}` plus a
`home-assistant/builder` workflow would fix that, but only once the workflow
really publishes - a config that points at an image that is not there is worse
than a build.


## Files are not counted (0.23.100)

0.23.98 removed the whole *Include ripped files* mechanism and left `counted =
base` in `library_stats`, so the catalogue's headline counted **every**
`format='ripped'` copy - 978 on the live shelf, 466 of them on films - while the
format pills underneath it still skipped them (`for row in formats if
row["fmt"] != "ripped"`). The film view read "636 titles · 1220 copies" above
pills that added up to 754, which is how it was spotted.

- `counted` is `base + " AND (i.format IS NULL OR i.format <> 'ripped')"`
  again, so the tally and the pills agree: films **754**, music **547**, the
  whole shelf **1305** (it read 2283).
- `titles` still comes from `base`, so a title holding only a route stays in
  the catalogue and can still be played. Keeping every title listed was the
  half of 0.23.98 worth keeping; counting its files as copies was not.
- A ripped **file** (`format='ripped'` with `rip_path` set) is excluded too, on
  purpose: it is its disc's file, not a second copy. None exist live
  (`itunes` 167 + `jellyfin` 978 routes, no `provider='rip'` rows).
- The pill loop's `if row["fmt"] != "ripped"` is now redundant, and kept only
  because it documents the intent.

Verified on a `VACUUM INTO` copy of the live database: `library_stats` gives
1182 titles / 1305 copies for the whole shelf, 636 / 754 for films and 542 /
547 for music, and in each case the format rows add up to the copy count;
`render_index` renders "636 titles · 754 copies"; `?format=bluray` gives 195
titles · 197 copies (191 of them films, matching that pill).

Trap: `sqlite3 /data/media.db ".backup /tmp/x.db"` streamed out of the container
gave a file that was the right size, answered queries, and failed
`PRAGMA integrity_check` (*Rowid out of order*) - it claimed 104 items with no
title row in a database whose live original has none, which nearly sent me
chasing a data-loss bug that does not exist. Use `VACUUM INTO '/tmp/x.db'` and
check `PRAGMA integrity_check` before believing anything a copy says.


## An owner filter narrows the card too (0.23.101)

Filtering the catalogue by owner already narrowed the titles *and* the copy
tally - Mitchell shows "158 titles · 183 copies", and 183 is exactly his own
movie copies (every copy of those 158 titles would be 315) - but each card's
format line came from a subquery in `render_index` that ignored the filter:

```
(SELECT group_concat(DISTINCT ai.format) FROM items ai
  WHERE ai.title_id = t.id
    AND (ai.visibility = 'shared' OR ai.owner_id = ?))
```

So *A Clockwork Orange* - Mitchell's 4K, Matt's digital - read
"1971 · 4K Blu-ray · Digital" on Mitchell's page. The subquery is now spliced
with `fmt_owner = " AND ai.owner_id = ?" if owner else ""`.

Trap: the card query's placeholders are read in **text** order, and the SELECT
list comes before the WHERE clause, so the params are

```
[viewer["id"]] + fmt_owner_params + [viewer["id"]] + extra
```

- the fmt_list viewer id, the owner, then the clause's own viewer id (which
  `visible_clause` leaves for the caller to supply). Putting the owner after
  the second viewer id binds it to the visibility test instead and silently
  lists the wrong copies.

`has_rip` (the play mark) is deliberately **not** owner-scoped: a Jellyfin route
belongs to the `rips` account, so scoping it would clear the mark from every
title the moment an owner filter is applied.

Verified on a `VACUUM INTO` copy: owner=7 -> 158 titles · 183 copies and
"1971 · 4K Blu-ray"; owner=2 -> 514 · 566 and "1971 · Digital";
owner=7&format=uhd_4k -> 136 · 161, matching that pill exactly; unfiltered
unchanged at 636 · 754 and "4K Blu-ray · Digital".


## The walk was missing a whole library (0.23.102)

Jeanine's 16 albums had **no** Jellyfin route, and never would have: `_index()`
listed the library with `/Items?Recursive=true&IncludeItemTypes=…&UserId=<admin>`,
and that account cannot see the `Jeanine's Music` folder, so those albums were
never in the index at all. Nothing about the name matching was wrong - a
`searchTerm` query (which passes no UserId) finds `Cross Road: The Best of Bon
Jovi` under `/media/QNAP/Music/Jeanine's Music/…` while the walk returned 0 of
her 16 albums.

Nor is either mode a superset of the other, which is why the old comment ("a
user id matters … without one Jellyfin lists a subset") was half right:

| type | with UserId | without |
|---|---|---|
| Movie | 886 | 658 |
| Episode | 4525 | 4525 |
| Audio | 10450 | 10799 |
| MusicAlbum | 836 | 853 |

`_everywhere(base, key, include, fields)` unions the two listings, deduped by
item id, and the five walks in `_index` use it. The union indexes 1018 movies,
4525 episodes, 10799 audio and 853 albums.

Measured on a `VACUUM INTO` copy of the live database: `sync_links(con, server=row)`
only **writes the links** - the materialise/expand/editions/dedupe steps live in
the outer `sync_links(con)` branch, so a dry run must call `sync_links(con, log)`
with no server, or run `materialise()` afterwards. Doing that, the copy gained
17 items, 16 of them Jeanine's albums (`_name_match` now misses 0 of her 16
titles, having missed all 16 before).

Live, after 0.23.102: 981 Jellyfin routes -> **998**, items 2283 -> 2300, a
second sync changes nothing (998 / 2300 again), and the catalogue's music tally
stays 542 titles · 547 copies - a route is not a copy, see *Files are not
counted*. Diffing the set of titles that hold a route against a copy taken
before the change gives exactly 17 titles: Jeanine's 16 (568-599) plus
`Minecraft - Volume Alpha` (#600), which Jellyfin holds under
`/media/QNAP/Music/Justin's Music/C418/…` - a second library the admin account
cannot see. Her Bon Jovi album page now carries a **Play** button and a
*Jellyfin matches* row.


## A play the device cannot see (0.23.103)

Jeanine's newly linked albums got a **Play** button, and pressing it said "Sent
to Jellyfin" while nothing played. The play command is validated against the
**session's** Jellyfin user, not our API key: the server accepts
`POST /Sessions/<id>/Playing` and the device then silently drops an item its
user cannot see - Matt's account cannot see `Jeanine's Music`.

- `jellyfin.access_reason(base, key, session_id, jf_id)` asks whether that
  session's user may see the item, and `play()` raises `JellyfinError` *before*
  sending anything, so the page banner reads "Jellyfin: Xiaomi 25053PC47G
  (Moonfin for Android 2.6.0) is signed in as Matt, who cannot see this title -
  give that user access to the library in Jellyfin, or play it from a device
  that has it."
- The one question that honours a user's library access is
  `GET /Users/<userId>/Items/<itemId>`. `/Items?Ids=<id>&UserId=<id>` answers
  from the API key's view and returns the item anyway - measured: 1 of 1 for
  both one of Matt's albums and one of Jeanine's - so it cannot be used here.
- `JellyfinError` now carries `status` (0 when the server never answered), and
  only a **404** counts as a refusal: a timeout or a 500 must never stop a play
  that would have worked. A session carrying no `UserId` is left for Jellyfin
  to refuse, as are the 404s for an unknown session.

Verified live with a real session (the Xiaomi phone, signed in as Matt):
`access_reason` returns the sentence above for Bon Jovi's album and `""` for a
title Matt can see, and `POST /title/573/jellyfin` answers 200 carrying that
sentence in the banner instead of "Sent to Jellyfin" - no play command reaches
the device.


## The "Read more" links had no gap (0.23.104)

Every artist page with a biography ended in `Read more:
WikipediaWikidataMusicBrainzDiscogs` - four underlined links run together,
which reads as one long word. `render_artist()` built them with

```python
sources = "".join('<a …>%s</a>' … for item in found)
```

and nothing in the markup separated them. It is `" \u00b7 ".join(…)` now, the
same ` · ` separator the info line above it uses ("NZ · born 1976").


## Titles file without their article (0.23.105)

`titles.sort_name` had been in the schema since 0001 and was **NULL on all 1182
rows**, so the catalogue ordered by `t.name`: "The Abyss" and "The A-Team" filed
under T, while the act list had always put "The Beatles" under B through
`artists.sort_name` ("The Beatles" -> "Beatles, The").

- `migrations/0025_title_sort_name.sql` fills the column with the same
  convention - The/A/An move to the end - and adds `titles_sort_name_on_insert`
  and `titles_sort_name_on_rename` (`AFTER UPDATE OF name`, so the migration's
  own UPDATE cannot fire it and a re-save that keeps the name is left alone).
  Every writer is covered without touching Python: `add_item`,
  `film_import`, `tv_import`, `music_import` (none of them mention the column),
  and both rename paths (`normalise.py`, the title form).
- `library_rows` and `render_index`'s card query order by
  `COALESCE(NULLIF(t.sort_name, ''), t.name) COLLATE NOCASE, t.name COLLATE
  NOCASE`; `artists.albums` uses the same key as the tie-break within a year.
- Nothing renders `sort_name` - it is a key, not a name, so the page still says
  "The Abyss".

Live: 197 of 1182 titles moved. The film grid now reads 10,000 B.C., 12 Monkeys,
127 Hours, 2 Fast 2 Furious, 2001 …, then Abraham Lincoln: Vampire Hunter,
**The Abyss**, Ace Ventura, and the first four "The" cards are The Abyss, The
Adventures of Tintin, The Amazing Spider-Man 2, The Amazing Spider-Man. The act
list is unchanged at 1 Giant Leap, ABBA, Adele … with The Beths at the B's.

Trap: a card's play mark sits **inside** `<strong>`, so scraping titles with
`<strong>([^<]*)</strong>` silently misses every playable title - it looked as
though "The Abyss" was missing from the page. Split on the mark, or strip the
trailing space.


## Numbers sort by value (0.23.106)

The 20 digit-led titles read "1, 1 Giant Leap, 10 000 Hz Legend, 10,000 B.C.,
12 Bar Bruise, 12 Monkeys, 127 Hours, 13, … , 2 Fast 2 Furious, 2001, 28 Days
Later, 300" - "10,000" before "127" before "2", which is neither alphabetical
nor numerical, because `COLLATE NOCASE` compares byte by byte.

`app/sorting.py` adds a `NATSORT` collation: a run of digits compares by value
(and then by length, so "7" < "007"), every other run compares case- and
accent-blind (`Björk` == `Bjork`, so she files with the Bs), and a number always
sorts before a word. It is used by `library_rows`, `render_index`'s card query,
`artists.all_artists`, `artists.titles_for` and `export_rows`.

- A collation belongs to a **connection** (`con.create_collation`), and a query
  naming one that connection does not know dies with `no such collation
  sequence: NATSORT`. `server.connect()` registers it, and those five query
  builders register it themselves, so a bare `sqlite3.connect()` handed in
  still works - which is how the render/verify scripts are written.
- Numbers first is the Unicode/ICU default and what Finder and Explorer do;
  Apple TV's "#" bucket at the end is a presentation choice, not a collation
  rule. The film grid now reads 2 Fast 2 Furious, 3:10 to Yuma, 10,000 B.C.,
  12 Monkeys, 28 Days Later, 127 Hours, 300, 300: Rise of an Empire, 2001: A
  Space Odyssey; the act list still starts 1 Giant Leap, ABBA, Adele.
- Cost: sorting 636 cards through a Python comparator is ~0.10 s per render
  (the whole grid page), the list view 0.06 s, the act list 0.004 s.


## Stores are data, and a store can open on a device (0.23.107)

A store was a hardcoded pair of keys in `server.py` (`PURCHASE_STORES = ("itunes",
"other")`) with labels in `ui_title.STORE_LABELS`, duplicated in two files, and
"iTunes" and "Apple TV" were folded into one key that rendered as "Apple TV".
Both are shops *and* places to play from, so the record now carries both.

- `migrations/0026_stores.sql` adds `stores(id, key, label, kind, target_id,
  app_name, is_active)`. `key` is what `digital_sources.provider` holds;
  `kind='store'` rows are offered in the pickers, `kind='system'` rows (jellyfin,
  rip) are keys the app writes itself. `target_id` points at a row of
  `playback_targets` - which has been in the schema since 0002, unused, with
  exactly the columns this needs (name, ha_entity_id, input_source, app_name).
- The migration also moves the 167 existing `itunes` rows to `appletv`: every
  one of them is a film bought in the Apple TV app, so `itunes` is free to mean
  the music store.
- `app/stores.py` is the whole model: `load/by_key/label/choices/resolve/add/
  update/remove`, plus `targets/target/add_target` for the devices and
  `playable_stores(con, digital)` for the Play sheet.
- **Free text widens the list**: the pickers are `<input list="store-choices">`
  with a `<datalist>`, not a `<select>`, and `stores.resolve()` matches a typed
  key *or* label case-insensitively before creating a row ("apple tv" ->
  `appletv`, no duplicate). Both forms (the catalogue's add dialog and the copy
  editor) go through it, for every format now, not only `digital`.
- `migrations/0027_store_keys.sql` rebuilds `digital_sources` **without** its
  `CHECK (provider IN ('rip','jellyfin','kodi','itunes','appletv','other'))`,
  which is what made a typed store fail with "CHECK constraint failed". SQLite
  cannot drop a constraint, so the table is recreated with the same columns,
  FKs and UNIQUE, and the view plus both indexes are put back. Watch the view:
  its last line is `LEFT JOIN media_roots r ON r.id = d.root_id`, which a
  truncated read misses - the first rebuild shipped a view without it and every
  query died on "no such column: r.label".

The Play sheet gained one `<section>` per store that names a device:
`jellyfin.playable_stores()` feeds `_play_block(base, row, ..., shops=[])` and
`ui_title._store_plays()` draws it. `POST /title/<id>/store` calls
`playback.open_on(entity_id, app, name)` - `media_player.turn_on` then
`media_player.select_source` - and answers with "Woke the Lounge Apple TV and
opened Apple TV." A purchased title cannot be deep-linked, so the sheet says to
pick the title on the device rather than pretending it queued something, and a
device that will not list its apps only warns (the wake-up still happened).

Trap: `_play_block` has **two** early returns, and the first one -
`if not playable and not jellyfin_ready: return (hint, "")` - fires for a title
with no file and no Jellyfin route, which is exactly the case a store-only copy
is. It needs `and not shops` too, or the button and the whole sheet never
appear while the store section silently does nothing.

Checked on a copy of the live database: 1148 rows preserved through the rebuild,
167 moved to `appletv`, `PRAGMA integrity_check` ok, the view answers 1148 rows,
`/admin` renders the Stores panel (5 rows, add + edit + remove forms) and
`/title/1` still renders. On a scratch DB: a copy added with the store typed as
"Bandcamp" lands as provider `bandcamp` and a new row appears in the pickers;
"apple tv" reuses `appletv`; with that store pointed at a device the Play sheet
shows "On the Lounge Apple TV" and "Open Apple TV", and the POST answers
"Could not wake Lounge Apple TV: <urlopen error timed out>" off the box.

