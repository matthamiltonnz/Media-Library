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
  (a `stores.key` - `appletv`, `applemusic`, … - or `jellyfin` / `rip`),
  `library_id`, `external_id`, `media_source_id`.
- **playback_targets** — the devices a later "play this" action will drive:
  `kind` (the entity's own domain: media_player / remote / input_select /
  script / automation), `ha_entity_id`, `input_source`, `app_name`.
- **stores** — the shops and platforms a copy came from or can be opened on:
  `key`, `label`, `kind` (`digital`, `retail`, `format`, `system`).
- **store_targets** — which player opens which shop, and the app it opens
  (`store_id`, `target_id`, `app`). A shop can run on several players.

One title can therefore have a Blu-ray, a download from a digital shop and a
Jellyfin file without them counting as duplicates.

## Library admin

The catalogue page carries a small **admin** link beside its title. That page holds the
occasional jobs:

- **Import a CSV** — staged review, as described above.
- **Export** — `export.csv` (every copy you can see, UTF-8 with BOM so Excel opens
  it cleanly) and `export.json` (the same rows as a backup-friendly document).
- **Maintenance** — folding duplicate titles the importers created, and
  **Clean database**: checks every recorded file and forgets the ones that have
  gone, asks each Jellyfin server which items it still holds and drops the copies
  whose item has been deleted there (or whose server row has gone), then prunes
  copies left with nothing. A copy still on a shelf, or still linked to a live
  item, is never touched — so it is safe to press on a shelf whose files all live
  on a Jellyfin box. A server that cannot be reached is left alone entirely,
  because an unreachable box must never look like an empty library.

Exports are per viewer: shared copies plus your own private ones.

- **Metadata** — one button, **Refresh metadata**, does both lookups at once:
  it walks every title missing data in the background (sequential, two requests
  per title: Wikipedia page search + summary) and fills
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

  The same button runs the act lookup as well — for every act its MusicBrainz
  id, country, start year and kind, then a Wikipedia biography, portrait and
  reading links — and both halves run **by themselves** too: on every start of
  the add-on sixty titles and sixty acts that are missing something are looked
  up in the background, and adding a title with *Look up metadata* ticked (it is
  ticked by default) fetches it there and then, a new act's MusicBrainz record
  and biography included. For an act already on the shelf that last step costs a
  single row read.

The catalogue opens in **Thumbnails** view (poster grid, one card per title with its
formats and copy count); the **List view** link next to the search box switches to
the table. The choice is carried through searches.

"Add a copy" takes an optional **IMDb ID**; tick *Look up metadata* and the new
title is created with its plot/poster/year straight away, landing you on its
detail page.

## Adding a title

*Add a title* on the catalogue page asks what the thing is first - **Media
type**: film, TV or music - and shows only the fields that type needs. Films and
TV get an **IMDb id** box; music gets **artist**, and the artist and release
MusicBrainz ids. The second group is copy detail whatever the type: format,
owner, store, visibility, edition, barcode and where it lives. Choosing a music
format (CD, vinyl, SACD, cassette) switches the media type to music by itself.

Ticking *Look up metadata* fetches plot, poster and year as the title is added.
Typing an IMDb id does the same without the tick, because an id means "look it
up" - and the year on the form is then replaced by the one the id carries.

The **Barcode** row has a **Scan** button - the rear camera reads the code on
the case, where the browser can do that (Chrome and Edge can; Safari cannot, and
says so) - and a **Look up** button for digits typed in by hand. A lookup fills
only the boxes that are still empty: an album barcode comes back from
MusicBrainz with its artist and year, anything else falls back to a product
database that usually knows the name on the box. A barcode already in the
catalogue is named in the line under the boxes, so the same disc is never
catalogued twice.

**The scanner needs a secure page.** Browsers hand out the camera only to
`https://` or `localhost`, and never to a framed page - so inside the Home
Assistant panel it is refused even when Home Assistant itself is HTTPS. Open the
catalogue on its published port (8099) over HTTPS instead. The add-on answers
plain HTTP and HTTPS on that one port, and offers HTTPS only when it finds an
**unexpired** certificate in `/ssl`, `/data/tls` or `MC_TLS_CERT` /
`MC_TLS_KEY`; putting it behind the reverse proxy you already run is the easier
route, since that keeps renewals automatic.

If that name and year are **already in the catalogue**, the title is not added:
a page names the existing title and offers to open it, because what you have is
almost certainly another copy of it - and copies belong on the title page,
where the catalogue can ask about a duplicate:

**Adding a copy** of something you already hold in that format (same owner) asks
first - *Sam already owns a Blu-ray of that film in Lounge shelf* -
with **Add another copy** and **Cancel**, and nothing is written until you say
so. A second copy is a real thing (one for the lounge, one for a bedroom), so
this asks rather than refuses. For music the artist decides as well as the
name: two acts can both have *Greatest Hits*, so a matching name under a
different artist is a new title, while the same name with no artist at all is
treated as the same album and refused.

## Copies

The **Copies** tab holds the detail and the editing: every copy of the title with
its format, store, owner, edition, region, shelf, condition, barcode, price paid,
purchase date, last watched date and visibility. Open a copy to change it, delete
it, or add another one (owner included) without leaving the page. A digital copy
records the shop it came from - where a disc was bought, or the digital shop a
copy was purchased in - next to what it cost and when. **Store** is empty ("-")
unless something really came from a shop, and the name is free text: a shop the
catalogue has not seen before becomes a row the first time you type it.

## Where things are

Every copy carries a **Location** - *Where it lives* on the add form: the room
and the furniture, not the address. It is free text, so write what makes sense
where you live - a shelf, a cupboard, a box in the garage. For example:

* **Lounge kallax** - every record, whoever owns it
* **Lounge shelf** - the films, TV and box sets
* **Bedroom storage** - the CDs

A download and a Jellyfin route have no location, because there is no shelf to
put them on. The list view shows a copy's location beside it.

## By artist

Music titles are stored as "Artist — Album", and that name is split once into an
`artists` row per act (`artists.py --link`, re-run on every add-on start and after
a music import). The Music view then offers **By artist**: every act with albums
on the shelf, how many, and the years covered, as a table or as a wall of covers.
Each act opens a page with their albums (again list or thumbnails), what each one
is owned as, and a MusicBrainz link, so a music title edits the **MusicBrainz ID**
instead of the IMDb/TMDb pair.

Artist details come from MusicBrainz without any Jellyfin involvement: **Admin →
Metadata → Refresh metadata** looks up every act that is missing a detail (one
request a second, in the background) and stores the MusicBrainz id, country and
the year the act started, then its Wikipedia biography; a few are filled in
automatically on each start, and adding an album fills a brand-new act at once.
Its progress
line sits on that panel only while the fill is running, so the page carries no
stale count. Since an artist
has no picture of its own, the page and the cover wall borrow the art of their
first album - Jellyfin's image when that album is linked, otherwise the Cover Art
Archive picture MusicBrainz gave it.

A biography sits above the albums. MusicBrainz has none — the only text it
offers is a disambiguation line ("Group") — so **Fill artist details** looks each
act up on Wikipedia once and stores the article's opening paragraph, its
portrait, and a *Read more* row of links. The page carries a **Fetch details**
button of its own, so a single act can be looked up (or re-looked-up, when the
match was the wrong one) without waiting for the whole job.

The article is found from the act's **MusicBrainz id**, not its name: the id
leads to the act's Wikidata item, and that names the English article. That is
what stops *Air* resolving to the gas rather than **Air (French band)**, and
*St Germain* to the place rather than the musician. Only an act with no id (or
one MusicBrainz has no links for) falls back to searching Wikipedia by name,
which is where a common-word name goes wrong.

*Read more* lists wherever else the act can be read about, taken from
MusicBrainz's own relationships rather than guessed: Wikipedia, Wikidata,
MusicBrainz, **Discogs** (the artist page, not a search), **AllMusic**,
**Last.fm**, **IMDb** and the act's **official site** when they exist, capped at
six. There is no keyless source of a longer biography than Wikipedia's lead
paragraph — Fanart.tv, TheAudioDB and the Last.fm/Discogs APIs all want a key —
so Wikipedia's extract is the text and the rest are links out.

A **MusicBrainz id** box sits under *Rename artist*, for the lookups that failed
or matched the wrong act. Paste the 36-character uuid from the artist's own
MusicBrainz URL and press *Save id*: the id is stored, its MusicBrainz record is
re-read for country, start year and kind (the fields a bad lookup leaves blank or
takes from the wrong act), and the old "read more" links are dropped so **Fetch
details** rebuilds them against the right act. A mistyped id is refused with a
red banner and nothing written; an empty box clears the id on purpose.

## Filtering

The format tally under the heading ("Blu-ray 172 · Digital 169 · 4K Blu-ray 38") is
clickable: press a format to see only those copies, press the same one again to
clear it. The tallies keep showing the whole library so you can switch between
them. Every card and list row carries the filters it was opened with, so **Back to
the catalogue** returns to the same kind, owner, search and format.

## Ripped files are copies too

A ripped file is a playable digital backup of media you own, so it becomes its own
copy of a title already in the collection — badged **Ripped file · whoever owns the
disc** — and never a title of its own: the scan only matches files against titles
that already have a copy. A disc keeps its own row untouched. Old scans had hung the
file off whichever copy came first, which hid it; the scan now moves those onto their
own copy (`repoint_rips`) on every run.

Titles whose only copies are files used to be hidden from the catalogue, with a
link under the tallies to bring them back; that switch was only ever there while
the first import was being sorted out and is gone. Every title is listed, and the
tallies count real copies: a `ripped` file is the disc's own file, not a second
copy, so the format pills and the headline agree.

## Play

A title page opens with the cover, a muted line of facts (year, kind, owner,
store) and the **Play** button — right under those facts and above the copies,
because starting a film should not mean scrolling past every copy of it first.
A title with nothing playable shows a plain hint in that same place ("No file to
play yet — rip the disc to a media folder…") and no button at all. The button
opens a small window with two labelled halves:
**From the shelf** picks a Home Assistant player and then the copy to start, and
**On the Jellyfin server** picks the Jellyfin app that is open right now (Apple TV,
Vero, a phone) and starts the linked item there. Nothing lists sessions on the page
itself - the picker is where they belong. Only players that accept a file
(`play_media`) and are reachable are listed, and **Admin → Players** narrows that
list to the handful you actually use.

Below those halves sits a section per **shop** that names a player (Apple TV,
Apple Music, Google TV, …) and one per **physical format** that does (Blu-ray,
vinyl, …). A shop's button wakes that player and opens the app the shop runs
there - an Android package (`com.example.app`) or a name the device lists; a
disc's says what it is doing and asks you to put the disc in, because a disc
cannot be deep-linked. Admin → **Stores and platforms** is where a shop, a
format and their players are wired together, each link carrying its own app.

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

### The pinned player

Once something is playing, a bar appears at the bottom of **every** page and
stays there while you browse: the cover, the name (a link back to the title),
"artist · album" or the device, a thin progress line, **Pause**/**Resume** and
**Stop**. It asks the add-on every five seconds and the add-on asks Jellyfin
(`/Sessions`), so playback started from Jellyfin's own app shows up too. The bar
hides itself when nothing is playing, and the page grows a little padding
instead of hiding its last row behind it.

The bar carries a **transport row**: previous, rewind, play/pause, fast-forward,
next and stop. Previous/next appear only while a track is playing (a film has
nothing to skip to), and rewind/fast-forward step whatever Jellyfin's own client
does with `Rewind`/`FastForward`. Stop stops the playback *and* closes the bar.

### Several players at once

Two players can have something on at the same time — a phone casting to the TV
counts as two sessions, and two rooms make two more. When that happens the bar
shows a small pill per player under the title, the one it is controlling filled
in green and any paused one dimmed; pressing another pill hands the controls
over. Without a second player the pills are not drawn at all.

The bar remembers the player it was switched to. If that player stops, the next
poll comes back with the best remaining session (a playing one before a paused
one, and the most recently active after that) and the bar simply carries on with
it, with that player's pill highlighted.

An album sends its **tracks**, not just its album id: Jellyfin's
`POST /Sessions/<id>/Playing` starts only the first track of an album item on
most clients, so `album_tracks()` lists the album's audio items in disc and
track order and names them all. A film keeps its single id.

## Playback sources (rips in a media folder)

Each copy can record where it can be played from: open a title → **Playback
sources**. Pick the copy, the provider (*Ripped file*, Jellyfin, or a shop) and
the **media root** plus the path inside it.

Roots are rows, not config — Admin → **Media roots** lists them, and every row is
editable in place (label and path) with **Save**, alongside **Remove**; a new
folder is an **Add** form underneath. Labels and paths must be unique, and a clash is
reported rather than silently dropped. A root is a folder the container can see
under `/media`, one row each - for example:

| Root | Path |
|---|---|
| Movies | `/media/movies` |
| TV | `/media/tv` |
| Music | `/media/music` |

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
**MP3 → Ripped · YouTube Music · MP3**, **M4A/AAC → Ripped · Apple Music · AAC** (the shop
is recorded in the copy's edition), and a folder that holds a concert disc (`.mka` /
`.mkv` / `.cue` and no separate tracks, e.g. an Atmos Blu-ray) becomes a **Blu-ray**
copy. Folders with no audio at all — artwork-only leftovers (`Folder.jpg`,
`desktop.ini`, `.db`) and NAS housekeeping (`.@__thumb`, `@eaDir`, `#recycle`) — are
skipped. Re-running it adds nothing, so it is safe after every new rip:

```bash
python3 /app/music_import.py /data/media.db "/media/music"            # import
python3 /app/music_import.py /data/media.db "/media/music" --dry-run  # count only
python3 /app/tv_import.py /data/media.db "/media/tv"                 # TV seasons
python3 /app/film_import.py /data/media.db "/media/movies" \
        --owner "<display name>"                                        # ripped films
```

The media root is the Home Assistant media folder (`/media`); everything below it
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
a Blu-ray.com export. The start-up call runs only when `FILMS_PATH` and
`FILMS_OWNER` are both set, and `--dry-run` prints what it would add.


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

## Importing a Blu-ray.com export is gone

The add-on no longer ingests a Blu-ray.com CSV. Media is taken from the
mounted media folders by the film, music and TV scans, and owned copies are added
by hand from a title page or the add-on panel.

---

## Title detail page

Click a title in the list. The page shows the poster, year, runtime, genres,
certification, IMDb/TMDB links and plot; every copy you own with an inline editor
(format, edition, barcode, region, location, condition, visibility, **last
watched**); the digital sources; and **Refresh metadata** plus a **Watched today**
button per copy.

An album's facts line carries the act as a **link to the artist page**, so
getting from "Homogenic" to the rest of Björk's shelf is one click — and the act
page's albums link back with the page they were opened from, so *Back* returns to
the artist rather than to the catalogue.

A small pencil in the corner of the poster opens a dialog with every image the
lookups have offered for the title (Wikipedia, Wikidata's IMDb/TMDB hand-off,
MusicBrainz covers) plus the Jellyfin still; click one to make it the image the shelf
shows, press the little **×** on a candidate to forget it for good (a poster fetched
for the wrong film otherwise stays in the list forever), or pick **Automatic** to go
back to "Jellyfin first, then the poster".
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
* **Jellyfin**: press *Add a server* on the admin page — a label, its URL and a key
  (Dashboard → API Keys) — then *Sync with Jellyfin*, and every copy is linked
  to the item Jellyfin already holds. The sync also runs once at start-up and
  whenever you press
  the button, so films added since the last one get their button without a visit to
  Admin; names are matched loosely (`10,000 B.C.` finds *10,000 BC*, and a catalogue
  subtitle may be dropped). The title page then offers *Play in Jellyfin*, which sends
  the film or album straight to a connected client. Servers and their keys are
  stored in the database (`jellyfin_servers`), so they survive upgrades; more
  than one can be registered, each keeping its own routes, and the add-on
  options carry no URL or API key any more — they are set on the admin page.
  **Edit** under a server changes its label, address or key in place: leave the
  key box empty to keep the key already stored (it is never shown), and run a
  sync afterwards so the copies pick up a new label.

## Updating a running install

`/data/media.db` is created once and never replaced. The image ships no catalogue at
all — a fresh install starts empty. On every start the add-on:

1. **normalises** titles that carry the format in their name: copies of
   `… 3D` / `… 4K` / `… Ultra HD` are flagged **3D Blu-ray** / **4K Blu-ray** and
   the suffix is stripped from the title (folding into an existing title of the
   same name and year if there is one).

All of these steps are idempotent and log a one-line summary (`normalise: N copies
flagged …`).

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

## Install

Two ways in, both ending with **Media Collection** in the sidebar.

### From the repository (recommended)

1. Settings → Add-ons → Add-on store → ⋮ → **Repositories**
2. Add `https://github.com/matthamiltonnz/Media-Library` and close the dialog
3. The store reloads; **Media Collection** now sits under *Media Library Add-ons*
4. Open it, press **Install**, then **Start**

The *Add repository to Home Assistant* badge in the README does the first two
steps for you.

The Supervisor builds the image on your own machine, from a Home Assistant base
image (`build.yaml` names the one for each architecture) plus Alpine's `python3`
and `sqlite`. No prebuilt image is published, so the first install needs
internet and takes a few minutes - longer on a Pi. Run
`python3 tools/check_addon_repo.py` before a release: it checks the descriptor
files, that every base image tag really exists in the registry, and that the
store artwork is the size Home Assistant expects.

`/media` is mounted read-only (`media:ro`) so the add-on can find ripped files;
the catalogue itself is `/data/media.db` inside the add-on.

### From a local folder (development)

Copy `media_collection/` to `/addons/media_collection`, then Settings → Add-ons →
Add-on store → ⋮ → **Reload**, and install **Media Collection** under *Local
add-ons*. Bump `version` in `config.yaml` for each change - the Supervisor only
rebuilds when it changes.

Both routes use the slug `media_collection`, so a machine should have one or the
other: remove the local copy before installing from the repository, or the store
lists two add-ons with the same slug.

## Data, backup, schema changes

`/data/media.db` (SQLite, WAL) is included in add-on snapshots. `migrate.sh` runs
on each start and records applied files in `schema_migrations`. The schema is one
file, `migrations/0001_schema.sql`, which builds a brand-new database on its own;
a database that already has tables is recorded as having it and left alone. Add a
new `0002_*.sql` beside it for a change, and never edit a file that has run.

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
2. ~~Home Assistant "play this" routing per `playback_targets`~~ (done - a shop
   or a format carries its players, and each link carries the app it opens)
3. ~~Jellyfin item matching via `digital_sources.external_id`~~ (done, versions
   and editions included)
4. ~~Open a shop's app on a device~~ (done - an Android package, or a name the
   device lists)
5. Still open: browsing a Jellyfin server's media folders from the link page
   (only search is offered), and a prebuilt image so an install does not have to
   compile on your own machine.
