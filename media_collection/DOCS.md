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
  `library_id`, `external_id`, `media_source_id`, and `media_quality` - what the
  file is, as the sync read it from Jellyfin: `1080p · DTS-HD MA 5.1 · 8.6 GB`,
  or `FLAC 2.0 · 975 kbps · 27 MB` for an album (whose files hang off its
  tracks, not the album item; an item holding one or two tracks says so -
`FLAC 2.0 · 975 kbps · 27 MB · 1 track` - because a single-file album would
otherwise look like a sizing bug).
- **playback_targets** — the devices a later "play this" action will drive:
  `kind` (the entity's own domain: media_player / remote / input_select /
  select / script / automation), `ha_entity_id`, `input_source`, `app_name`.
- **stores** — the shops and platforms a copy came from or can be opened on:
  `key`, `label`, `kind` (`digital`, `retail`, `format`, `system`).
- **store_targets** — which player opens which shop, and the app it opens
  (`store_id`, `target_id`, `app`). A shop can run on several players.
- **collections** — a film or TV series (Fast & Furious, Alien): `name`,
  `sort_name`, `kind`, `overview` (what the series is), `notes`, `poster_url`.
  A title points at one with `titles.collection_id`, and `collection_order` is
  its place in the series - NULL sorts after the numbered ones, by year.
  `titles.series_at` is when Wikidata was asked which series the title belongs
  to, so a film that belongs to none is only asked about once.

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

- **Metadata** — one button, **Refresh metadata**, does every lookup at once:
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
  single row read. On top of that the whole pass repeats itself **once a day at
  03:00** the household's time - the app takes its clock from Home Assistant's
  time zone, so this is 3 am where you are, not 3 am UTC
  (`refresh_hours` - 24 daily, 168 weekly, 0 never; see *Options*),
  working through up to two hundred titles (and two hundred acts) a night, so a
  shelf nobody touches still drains its backlog. The start-up pass stays at
  sixty: a restart is cheap, a nightly sweep can afford to be patient.

  **Collections are refreshed by that button and by the nightly pass too.** For
  every collection: its films are listed from Wikidata (that is the *Films in
  this series* gap list on its own page), its paragraph and cover come from the
  series' Wikipedia article, and any film of the series that is on the shelf but
  in no collection is **filed into it** from that list — so a *Fast & Furious*
  series picks up *Hobbs & Shaw* without anyone dragging it in. A film somebody
  filed elsewhere is never moved, and a film with no year that matches nothing
  is left alone. The two buttons on the collection page (*Look up the films in
  this series*, *Refresh metadata*) run the same pass for that one collection.

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

**A disc barcode is the hard case.** MusicBrainz only files albums, and the
keyless product database has far more groceries than Blu-rays, so a retail case
is often in neither. The page says which services were asked and offers a web
search for the code ("9338176028766 blu-ray"), which is what finds a disc; typing
the title off the case is quicker still. Both the 13-digit EAN and the 12-digit
UPC form of a code are tried, and UPCitemdb's fuzzy search runs when its exact
lookup misses.

One shop does file discs by barcode: **imusic.com** redirects `/movies/<upc>` (or
`/music/<upc>`) to a slug carrying the year and title ("2024-madame-web-blu-ray"),
so the add-on asks it once per barcode, in one request a minute, and only when
everything else has missed. Their firewall answers 418 when it is asked too
often - and that bans the household's address for a while, which is why the
pacing is deliberate and why a refusal is reported ("try again in a minute")
rather than cached as "no such product".

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

## Artists

Music titles are stored as "Artist — Album", and that name is split once into an
`artists` row per act (`artists.py --link`, re-run on every add-on start and after
a music import). The Music view then offers **Artists**: every act with albums
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

## Collections

A **collection** is a film series - *Fast & Furious*, *Alien*, the Cornetto
trilogy - and it is one row (`collections`) that several titles point at, the
same shape as an artist and its albums. It exists because a search cannot see a
series: the eighth Fast & Furious film is *Hobbs & Shaw* and shares not one word
with the other seven.

- **Collections**, on the film and TV views of the catalogue, opens the list of
  every collection with a search box and a wall of covers. A collection's cover
  is its own picture if it has one, else the first film's poster.
- **A search finds the shelf, not only the discs.** Type `alien` on the film
  view and the first card is **Alien - Collection · 6 films · 1979–2024**,
  linking to the series; the films inside follow. Type `tame` on the music view
  and the first card is **Tame Impala - Artist · 4 albums · 2012–2025**. The
  card is title-shaped (a cover, a name, a caption) but it opens a page, and it
  obeys the media-type filter: no artist under Movies, no collection under
  Music. In list view they are the first rows, with *Artist* or *Collection* in
  the Format column.
- A collection with **one film in it gets no card** - one film is not a shelf to
  browse - but that film still answers to the series' name, so `q=phase three`
  finds *Ant-Man and the Wasp* through its collection. Clicking a collection
  card carries the search into the collection page's **own** search
  (`/collection/18?q=marvel`), which narrows the series to the films that match
  and says "Showing 2 of 6 films matching …" with a *clear the search*
  link. A collection name on a film's page does the same, so the search
  follows you from the catalogue through a film to its series.
- A collection's card is drawn as a **stack of cases**: the cover in front with
  two more cases peeking out above and to the right of it, which keeps the
  artwork's lower edge in line with the film cards beside it. Title cards stay
  single cases, so a series can be told from a film at a glance.
- One collection's page lists every film in it, numbered, in watching order:
  `titles.collection_order` when it is set, then the year, then the name. Each
  card opens the film, and *Back* returns to the collection.
- A film joins a collection on its own page, under **Collection**: pick one from
  the list or type a new name - a series can be started from whichever film is
  in hand - and say where it comes in the series. Picking `-` takes it out
  again. The collection then shows in the line under the title.
- **Add a film to this collection**, behind a button at the bottom of a
  collection, searches the shelf for a film that is not in it yet - the box is
  hidden until you press it, and it has a field of its own (`?find=`), so the
  page's search above it and this one never overwrite each other. That is how
  *Hobbs & Shaw* gets in. A film already in another collection says so, and Add
  moves it.
- **Films in this series**, under the wall, lists what the series has that the
  shelf does not. Press *Look up the films in this series* and the catalogue
  asks Wikidata which films point at the series item (`P179`, the reverse
  direction), keeps the answer, and shows the ones nobody here owns, each with
  its year and a link to its own record. A film the household owns but has filed
  under another collection is named too, so you can see it is not lost. Nothing
  is fetched until you press it, and pressing it again refreshes the list - a
  new film in the series appears the day Wikidata knows about it.
- **Refresh metadata**, under the paragraph, fills the two things a collection
  cannot work out for itself - what the series *is*, and a cover - from the
  series' own Wikipedia article (reached through its Wikidata item). It fills
  only what is empty: a paragraph typed in *Edit this collection* is never
  replaced, and a cover already chosen is kept. Jellyfin artwork still wins over
  it while a linked copy has any, so the scraped picture is the fallback for a
  collection with no links. There is no other keyless source for a series -
  TMDB's collection data needs an API key this add-on does not have.
  Both buttons also do the *other* half — each is the whole refresh for this
  one collection: the film list is looked up again, and any film of the series
  that is on the shelf and in no collection is filed into this one (a film
  already in *another* collection is never moved). The nightly pass and the
  admin's **Refresh metadata** do the same for every collection.
- **Edit this collection** renames it and says what the series is (one
  paragraph, shown at the top of its page). **Delete this collection** removes
  the collection and leaves every film on the shelf.
- **A film files itself when metadata is refreshed.** Wikidata's *part of the
  series* (P179) is the only keyless route to a franchise - TMDB's collection
  field wants an API key - so a refresh asks it and puts the film in the series
  it names, creating the collection if it is new. Two guards: a collection
  someone chose by hand is **never** replaced, and `titles.series_at` records
  that the question was asked, so a film that is genuinely part of no series is
  not asked about again. Asking is not free (three requests), which is why it
  happens on refresh rather than on every page: *Refresh metadata* on the admin
  page walks the whole shelf in one pass, and the button on a film's page
  (*Look up its collection*) asks about that one title again.
- **No overwrite needed.** A film that already has its metadata but no
  collection is picked up by *Refresh metadata* for its series alone - two or
  three requests, not a fresh copy of its poster, plot and year - so the
  "also overwrite titles that already have metadata" tick is no longer the only
  way to file an existing library. The job alternates the two kinds of work, so
  the sixty-title pass the add-on runs at start-up makes progress on both.
- P179 is not a tidy field. *Inception* is "part of the series BBC's 100
  Greatest Films of the 21st Century", and *The Dark Knight* is both "Batman in
  film" and "The Dark Knight trilogy", so a candidate only counts when its own
  class is a kind of film series (not a list article) and the widest one wins
  (Star Wars over "Star Wars original trilogy"). It is still a guess: it files
  *Batman Returns* under "Batman", which is right, and *Batman: Mask of the
  Phantasm* under nothing at all, which is honest but useless. Rename, merge or
  delete what it gets wrong - a collection is a row, not a fixed fact.

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
copy, so nothing is counted twice.

## Play

A title page opens with the cover, a muted line of facts (year, kind, owner,
store) and the **Play** button — right under those facts and above the copies,
because starting a film should not mean scrolling past every copy of it first.
A title with nothing playable shows a plain hint in that same place ("No file to
play yet — rip the disc to a media folder…") and no button at all. The button
opens a small window with two labelled halves:
**Ripped or downloaded copy** - drawn only when one is linked, and named *Digital
copy* when a shop is all there is - plays a file that is already there: a Jellyfin
route or a media-folder file, ripped from a disc you own or downloaded from a shop.
**From Media folder** picks a Home Assistant player
and the file to start, **On the Jellyfin server** picks the Jellyfin app that is open
right now (Apple TV, Vero, a phone) and starts the linked item there, and a shop
attached to a player wakes that player and opens its app. Under the Jellyfin
heading the sheet also prints **what the file is** - `4K · Dolby Vision · DTS-HD MA
5.1 · 73.1 GB`, or `FLAC 2.0 · 975 kbps · 27 MB` for an album - so you can decide
whether the rip is as good as the disc before walking to the shelf; a title with
several Jellyfin versions says it for each one in the version picker. **Physical
copy** plays the disc or record on the shelf: a **Player** picker when the format
has several players (three activities on one Harmony Hub, say), a single button
when it has one. The player sets the room up - *Turntable Speakers* on a vinyl
copy - and the sheet tells you to put the disc in.
Nothing lists sessions on the page itself - the picker is where they belong. Only
players that accept a file
(`play_media`) and are reachable are listed, and **Admin → Home Assistant
Players** narrows that list to the handful you actually use.

Below those halves sits a section per **shop** that names a player (Apple TV,
Apple Music, Google TV, …) and one per **physical format** that does (Blu-ray,
vinyl, …). A shop's button wakes that player and opens the app the shop runs
there - an Android package (`com.example.app`) or a name the device lists; a
disc's sets the room up and asks you to put the disc in, because a disc
cannot be deep-linked. A section with **several** players shows one
**Player** picker and a single button (three activities on one Harmony Hub),
instead of a button for each; with one player the button is labelled with
its name. The note underneath names the option when every player sets the
same one ("Sets the player to Turntable Speakers"), says *Sets the player
you pick* when they differ, and warns when a `select` player has no option
stored at all. Admin → **Stores and platforms** is where a shop, a format
and their players are wired together, each link carrying its own app.

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
(an *Other* pill appears only if such titles exist). Filtering runs **top down**:
the search narrows every row beneath it, the mode you pick narrows the formats and the
owners, and a format narrows the owners — but no row is narrowed by a row below it. So
each row is a map of what you can reach from there: the mode row always carries the
shelf's **full title counts** (Movies 634 · TV 4 · Music 560), the format row counts
that mode's titles, and the owner row counts the titles left after the search, the mode
and the formats. *What is actually on the page* is the headline above the cards —
"N titles · M copies". Under the modes, a second row of pills
lists **Everyone** and every household member who owns a copy (even with nothing in
the current mode, where the count shows 0), each with their copy count inside the
current mode; other people's private copies never appear, whichever filter is active.
When nothing survives the filters the page says so instead of claiming the shelf is
empty — *Nothing matches these filters. Clear them.* — and a format you have picked
stays in its row even when this mode has none of it (3D Blu-ray on the music shelf
reads **3D Blu-ray 0**, and clicking it takes the filter off), so an empty page always
shows which pill to press.
Within a mode the page shows the title/copy counts, and a **format pill row** sits
between the mode pills and the owner chips (CD, 3D Blu-ray, Blu-ray, 4K Blu-ray, …).
A pill applies the moment it is clicked; pick several to see "Blu-ray **or** 4K" at
once, and the count inside each pill is the number of **titles** of this mode with a
copy in that format — the search and the mode narrow it, a format or an owner picked
below does not (that is what top-down means). A title holding a 4K and a Blu-ray is in
both pills, and hover a pill to see how many copies its titles hold. Picking a pill
never removes the others, so a second format is one more click away; clicking the
current pill clears it.
Format pills stack on top of the search and owner filters, and a poster keeps showing
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

The pencil on the corner of a cover changes that choice. It lists every image a
lookup has offered so far (a title with no cover at all gets a *Choose cover art*
button in the same place), each with a small x to forget it, plus *Automatic*
(Jellyfin first, then the poster).

Two more ways to get a picture, both at the bottom of that sheet:

- **Search for other covers** opens a page of every cover the free sources hold.
  Albums come from the iTunes Search API (one picture per edition the shop sells)
  and from MusicBrainz, whose editions the Cover Art Archive answers for one at a
  time - so a deluxe edition's sleeve is there to pick, not just the standard
  one's. Films and seasons come from Wikipedia, whose articles each carry a
  poster. Nothing is copied: the page lists urls and the browser draws them.
- **Upload an image file** (JPEG, PNG, WebP or GIF, up to 6 MB) keeps the picture
  with the catalogue, under `/data/covers`, and serves it at `/cover/<file>`. It
  survives the source going away, which is what a photographed sleeve needs.

For music, a stored `musicbrainz_id` that names a *release* (one edition) now
gives that edition's own front cover from the Cover Art Archive; only when that
release has none does the album's standard cover stand in. A cover picked by hand
is never overwritten by a refresh - the fetched one is offered in the sheet
instead.

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
| `refresh_hours` | hours between automatic metadata passes: `24` daily, `168` weekly, `0` only when asked | `24` |

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
new `00nn_*.sql` beside them for a change - the next number in
sequence - and never edit a file that has run.

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
   (only search is offered); picking the fuller of two Jellyfin items that
   carry the same album (a one-track folder can shadow the real release);
   and a prebuilt image so an install does not have to compile on your own
   machine.
