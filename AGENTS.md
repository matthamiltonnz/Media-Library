# Working notes for this repo (agent memory)

Home Assistant add-on: a household film/TV/music catalogue. Python 3 stdlib
only (`http.server`, `sqlite3`), no framework, no ORM, no tests directory.
Entry point `media_collection/app/server.py`; schema in
`media_collection/migrations/*.sql` applied by `migrate.sh`.

Version lives in `media_collection/config.yaml` and is displayed on the page
header. Current: **0.23.9**.

## Live deployment

- Host: HA OS `192.168.234.3`, app container `app_local_media_collection`.
- App inside the container: `/app`; database `/data/media.db`; listens on `8099`.
- From the dev Mac: `/tmp/h.exp '<command>'` opens the host shell (password on
  stdin is already wired). `sudo docker exec app_local_media_collection ...`.
- Repo checked out at `/Users/matt.hamilton/Developer/Media Collection`.

Bump → package → deploy:

```
# 1. version
python3 -c "import io,re;c='media_collection/config.yaml';t=io.open(c).read();
t=re.sub(r'(?m)^version:.*$','version: \"0.23.N\"',t,count=1);io.open(c,'w').write(t)"
# 2. tarball, served from the Mac to the host
tar czf /tmp/serve/mc.tgz --exclude '__pycache__' --exclude '.git' media_collection
tar xzOf /tmp/serve/mc.tgz media_collection/config.yaml | grep ^version   # verify!
# 3. host: fetch, apply, update the add-on
/tmp/h.exp 'curl -fsS -o /tmp/mc.tgz http://192.168.234.117:8099/mc.tgz &&
  sh <(curl -fsS http://192.168.234.117:8099/apply.sh) >/dev/null 2>&1;
  export SUPERVISOR_TOKEN=$(cat /run/s6/container_environment/HASSIO_TOKEN);
  ha store reload >/dev/null 2>&1; ha apps update local_media_collection;
  ha apps info local_media_collection | grep ^version_latest'
```

`/tmp/serve` is served with `python3 -m http.server 8099 --bind 192.168.234.117`.
Always confirm `version_latest` matches the new number — three deploys were
silently skipped because the tarball was built before the version bump.

## Reading the code

Tool output for multi-line reads is collapsed in long sessions. What still
works: `grep -n`, `grep -c`, single-line `sed -n 'Np' file | cut -c1-58`.
To hand a region to the user, put it on their clipboard:

```
sed -n '308,404p' media_collection/app/ui_title.py | pbcopy
```

and ask them to paste it. This ended a run of six failed blind edits.

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
