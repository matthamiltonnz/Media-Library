"""Keyless metadata: Wikidata for the film item (year, IMDb id, Wikipedia page)
and the Wikipedia REST summary for the plot, poster and one-line description.

Everything is best-effort - a failed lookup returns nothing and the caller keeps
whatever is already stored.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import boxsets       # the collections a film series becomes; imports no metadata

UA = "media-collection-addon/0.9 (https://github.com/matthamiltonnz/Media-Library)"
FORMAT_SUFFIX = re.compile(r"\s+(3D|4K|Ultra HD)$", re.I)
YEAR = re.compile(r"\b(18|19|20)\d{2}\b")


class Throttle(Exception):
    """Wikimedia asked us to slow down (HTTP 429)."""


def remember_art(con, title_id, source, url):
    """Add a poster url to the title's remembered candidates (one row per url)."""
    row = con.execute("SELECT art_candidates FROM titles WHERE id = ?",
                      (title_id,)).fetchone()
    if row is None:
        return
    try:
        candidates = json.loads(row["art_candidates"] or "[]")
    except ValueError:
        candidates = []
    if any(item.get("url") == url for item in candidates):
        return
    candidates.append({"source": source, "url": url})
    con.execute("UPDATE titles SET art_candidates = ? WHERE id = ?",
                (json.dumps(candidates[-12:]), title_id))


def clean_title(name):
    return FORMAT_SUFFIX.sub("", name or "").strip()


def _key(text):
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _act_key(text):
    """A name's loose key with a leading "The" dropped - "The Prodigy" == "Prodigy"."""
    words = (text or "").strip().lower().split()
    if len(words) > 1 and words[0] == "the":
        words = words[1:]
    return _key(" ".join(words))


def _get(url, timeout=25):
    request = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        if exc.code in (429, 503):
            raise Throttle(url.split("?")[0])
        raise
    except urllib.error.URLError as exc:
        raise Throttle(str(exc.reason))         # offline / DNS hiccup: back off too


def wikipedia_search(name, limit=5, year=None, kind="movie"):
    """Page title on en.wikipedia for "<name> film" / "<name> TV series"."""
    suffix = "TV series" if kind == "tv" else "film"
    params = urllib.parse.urlencode({"q": "%s %s" % (name, suffix),
                                     "limit": str(limit)})
    data = _get("https://en.wikipedia.org/w/rest.php/v1/search/page?" + params)
    wanted = _key(name)
    best = None
    for page in data.get("pages", []):
        title = page.get("title") or ""
        base = title.split("(")[0].strip()
        if _key(base) != wanted:
            continue
        if not year:
            return title
        # "3:10 to Yuma" is the 1957 film, "3:10 to Yuma (2007 film)" the remake:
        # the year on the record has to win over the bare title.
        if "(%d" % year in title:
            return title
        blob = " ".join([page.get("description") or "",
                         page.get("excerpt") or ""])
        if str(year) in blob:
            return title
        best = best or title
    return best


def qid_by_imdb(imdb_id):
    """Wikidata item whose P345 (IMDb id) matches - the reliable way to resolve."""
    if not imdb_id:
        return None
    params = urllib.parse.urlencode({
        "action": "query", "format": "json", "list": "search", "srlimit": "1",
        "srsearch": 'haswbstatement:P345=%s' % imdb_id,
    })
    try:
        payload = _get("https://www.wikidata.org/w/api.php?" + params)
    except Exception:
        return None
    hits = payload.get("query", {}).get("search", [])
    return hits[0]["title"] if hits else None


def qid_by_tmdb(tmdb_id, kind="movie"):
    """Wikidata item whose P4947 (TMDB film id / P4983 for TV) matches.

    Lets a TMDB id typed in by hand do the resolving - no API key needed.
    """
    if not tmdb_id:
        return None
    prop = "P4983" if kind == "tv" else "P4947"
    params = urllib.parse.urlencode({
        "action": "query", "format": "json", "list": "search", "srlimit": "1",
        "srsearch": "haswbstatement:%s=%s" % (prop, tmdb_id),
    })
    try:
        payload = _get("https://www.wikidata.org/w/api.php?" + params)
    except Exception:
        return None
    hits = payload.get("query", {}).get("search", [])
    return hits[0]["title"] if hits else None


def search_wikidata(names, limit=10, workers=6, pause=0.0, kind="movie"):
    """{title: qid} - best film-ish (or TV-ish) item for each name, concurrently."""
    from concurrent.futures import ThreadPoolExecutor

    def one(name):
        params = urllib.parse.urlencode({
            "action": "wbsearchentities", "format": "json", "language": "en",
            "uselang": "en", "type": "item", "limit": str(limit), "search": name,
        })
        try:
            payload = _get("https://www.wikidata.org/w/api.php?" + params, timeout=20)
        except Exception:
            time.sleep(1.0)
            return name, None
        finally:
            time.sleep(pause)
        best = None
        for hit in payload.get("search", []):
            label = (hit.get("label") or "").strip()
            description = (hit.get("description") or "").lower()
            wanted = ("series", "television") if kind == "tv" else ("film",)
            if not any(word in description for word in wanted):
                continue
            # "film director", "film studio", "film producer" are not the film
            if any(word in description for word in
                   ("director", "producer", "screenwriter", "actor", "actress",
                    "studio", "company", "filmmaker", "critic", "festival")):
                continue
            if _key(label) == _key(name):
                return name, hit["id"]
            best = best or hit["id"]
        return name, best

    chosen = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for index, (name, qid) in enumerate(pool.map(one, names), start=1):
            if qid:
                chosen[name] = qid
            if index % 100 == 0:
                print("  searched %d/%d" % (index, len(names)), flush=True)
    return chosen


def fetch_entities(qids):
    """{qid: (year, imdb_id)} from the P577 / P345 claims."""
    found = {}
    qids = [q for q in qids if q]
    for start in range(0, len(qids), 50):
        params = urllib.parse.urlencode({
            "action": "wbgetentities", "format": "json",
            "props": "claims", "ids": "|".join(qids[start:start + 50]),
        })
        try:
            payload = _get("https://www.wikidata.org/w/api.php?" + params, timeout=60)
        except Exception:
            continue
        for qid, entity in payload.get("entities", {}).items():
            claims = entity.get("claims", {})
            dates = [c["mainsnak"]["datavalue"]["value"]["time"]
                     for c in claims.get("P577", [])
                     if c.get("mainsnak", {}).get("datavalue")]
            imdbs = [c["mainsnak"]["datavalue"]["value"]
                     for c in claims.get("P345", [])
                     if c.get("mainsnak", {}).get("datavalue")]
            year = None
            if dates:
                try:
                    year = int(min(dates).lstrip("+")[:4])
                except ValueError:
                    year = None
            imdb = imdbs[0] if imdbs else None
            if imdb and imdb.startswith("nm"):
                # nm… is a person (director, actor) - never the film itself
                found[qid] = (None, None)
                continue
            found[qid] = (year, imdb)
    return found


SERIES_CLASSES = ("film series", "film trilogy", "film tetralogy",
                  "film pentalogy", "film hexalogy", "trilogy", "tetralogy",
                  "pentalogy", "hexalogy", "film franchise", "film saga",
                  "series of films")

# Wikidata files the Marvel films under their *phase* ("Marvel Cinematic
# Universe Phase Two"), which is a finer division than a shelf wants: a
# household reads those as one series, so the trailing phase is dropped.
PHASE = re.compile(r"\s+Phase\s+[A-Za-z0-9]+$", re.IGNORECASE)


def _entities(qids):
    """{qid: entity} with claims and English labels, in one request."""
    qids = [qid for qid in dict.fromkeys(qids) if qid][:50]
    if not qids:
        return {}
    url = ("https://www.wikidata.org/w/api.php?action=wbgetentities"
           "&format=json&props=claims|labels&languages=en|mul&ids="
           + urllib.parse.quote("|".join(qids)))
    return _get(url).get("entities") or {}


def _label(entity):
    # Wikidata is migrating labels to the multilingual "mul" language, so an
    # item that is perfectly well named can have no `en` label at all - asking
    # for `en` alone silently returned "" for e.g. the film *Alien* (Q103569)
    for language in ("en", "mul", "en-gb"):
        value = ((entity or {}).get("labels") or {}).get(language) or {}
        if value.get("value"):
            return value["value"]
    return ""


def _claim_ids(entity, prop):
    """The Q-ids an item gives for one property (P179, P31, P527...)."""
    out = []
    for claim in ((entity or {}).get("claims") or {}).get(prop, []):
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(value, dict) and value.get("id"):
            out.append(value["id"])
    return out


def _claim_text(entity, prop):
    out = []
    for claim in ((entity or {}).get("claims") or {}).get(prop, []):
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(value, dict) and value.get("text"):
            out.append(value["text"])
    return out


def _claim_strings(entity, prop):
    """The plain string values an item gives for one property (P18 and the like)."""
    out = []
    for claim in ((entity or {}).get("claims") or {}).get(prop, []):
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if isinstance(value, str) and value.strip():
            out.append(value.strip())
    return out



def fill_series(con, title_id, force=False):
    """Ask for one title's film series and say, in a sentence, what happened.

    `refresh_job` walks titles whose own metadata is complete but whose
    collection is not known. This is the whole of that work, so the job can
    ask about a series without re-fetching and overwriting the metadata of an
    entire shelf.
    """
    try:
        made = autofill_series(con, title_id, force=force)
    except Throttle:
        return "rate-limited"
    except Exception as exc:
        return "error: %s" % exc
    return "filed under %s" % made if made else "no series"


def _claim_years(entity, prop="P577"):
    """The years an item dates something to (publication date, usually)."""
    out = []
    for claim in ((entity or {}).get("claims") or {}).get(prop, []):
        value = ((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        text = value.get("time") if isinstance(value, dict) else ""
        if isinstance(text, str) and text[1:5].isdigit():
            out.append(int(text[1:5]))
    return out


_CLASS_LABELS = {}


def _class_labels(qids):
    """{qid: English label} for the series classes, remembered per process.

    The same handful of classes - "film series", "film trilogy", "media
    franchise" - come back for every film, and Wikidata rate-limits (HTTP 429,
    which stops the job for ten minutes). Asking for each of them once turns
    four requests per title into three.
    """
    missing = [qid for qid in dict.fromkeys(qids)
               if qid and qid not in _CLASS_LABELS]
    if missing:
        for qid, entity in _entities(missing).items():
            _CLASS_LABELS[qid] = _label(entity)
    return {qid: _CLASS_LABELS.get(qid, "") for qid in qids if qid}


def series_of(imdb_id=None, qid=None, year=None, name=None, kind="movie"):
    """The film series Wikidata files this title under - [] when it files none.

    P179 ("part of the series") is the only keyless route to a franchise: TMDB's
    collection field wants an API key and MusicBrainz knows albums, not films.
    It is not a tidy field - *Inception* is "part of the series BBC's 100
    Greatest Films of the 21st Century", and *The Dark Knight* is both "Batman
    in film" and "The Dark Knight trilogy" - so a candidate only counts when its
    own P31 class is some kind of film series, and the widest one wins (Star
    Wars over "Star Wars original trilogy", by how many parts it lists).

    Three requests at most: the film, its series, and their classes.
    """
    if not qid and imdb_id:
        qid = qid_by_imdb(imdb_id)
    if not qid and name:
        cleaned = clean_title(name)
        qid = search_wikidata([cleaned], kind=kind or "movie").get(cleaned)
    if not qid:
        return []
    film = _entities([qid]).get(qid)
    if year:
        # a name search can land on the wrong film of the same name; the
        # publication date is the cheap way to tell them apart
        dates = _claim_years(film)
        if dates and min(abs(int(year) - one) for one in dates) > 1:
            return []
    series_ids = _claim_ids(film, "P179")
    if not series_ids:
        return []
    series = _entities(series_ids)
    classes = []
    for entity in series.values():
        classes += _claim_ids(entity, "P31")
    kinds = _class_labels(classes)
    order = (_claim_text(film, "P1545") or [""])[0]
    found = []
    for series_id, entity in series.items():
        label = _label(entity).strip()
        if not label:
            continue
        name = PHASE.sub("", label).strip() or label
        words = [(kinds.get(cls) or "").lower()
                 for cls in _claim_ids(entity, "P31")]
        if not any(word in kind for kind in words for word in SERIES_CLASSES):
            continue
        found.append({"name": name, "qid": series_id,
                      "parts": len(_claim_ids(entity, "P527")),
                      "order": order})
    found.sort(key=lambda item: (-item["parts"], len(item["name"])))
    return found[:1]


def series_members(qid, limit=60, kind="movie"):
    """Every film Wikidata files under one series item, oldest first.

    `P179` runs from a film *to* its series, so the reverse needs a search:
    CirrusSearch's `haswbstatement:P179=<qid>` answers with the item ids, then
    one `wbgetentities` turns those into names, years and IMDb ids. Only what is
    classed as a film survives - a series item also attracts *list articles*
    ("list of Fast & Furious characters"), and a gap list of those is nonsense.
    Two or three requests, a second apart; returns `[{qid, name, year, imdb_id}]`
    and `[]` for a series Wikidata knows nothing about.
    """
    if not qid:
        return []
    url = ("https://www.wikidata.org/w/api.php?action=query&list=search"
           "&format=json&srnamespace=0&srlimit=%d&srsearch=%s"
           % (max(1, min(int(limit), 200)),
              urllib.parse.quote("haswbstatement:P179=%s" % qid)))
    hits = ((_get(url).get("query") or {}).get("search")) or []
    ids = [hit.get("title") for hit in hits if hit.get("title")]
    if not ids:
        return []
    time.sleep(1.0)
    entities = _entities(ids)
    wanted = "series" if (kind or "movie") == "tv" else "film"
    classes = _class_labels([cls for item_id in ids
                             for cls in _claim_ids(entities.get(item_id), "P31")])
    found = []
    for item_id in ids:
        entity = entities.get(item_id) or {}
        words = [(classes.get(cls) or "").lower()
                 for cls in _claim_ids(entity, "P31")]
        if not any(wanted in word for word in words):
            continue
        name = _label(entity).strip()
        if not name:
            continue
        years = _claim_years(entity)
        imdb = (_claim_text(entity, "P345") or [""])[0]
        found.append({"qid": item_id, "name": name,
                      "year": min(years) if years else None,
                      "imdb_id": imdb or None})
    found.sort(key=lambda item: (item["year"] or 9999, item["name"]))
    return found


def autofill_series(con, title_id, qid=None, force=False):
    """Give a film its collection from Wikidata, once, never over a hand's pick.

    A collection someone chose by hand is left alone (only an empty one is
    filled), and `titles.series_at` records that the question was asked, so a
    film that is genuinely part of no series is not asked about again. `force`
    asks again anyway - that is what the button on a film's page does.

    Returns the collection name it filed the title under, or "".
    """
    row = con.execute("SELECT id, name, kind, imdb_id, release_year,"
                      " collection_id, series_at FROM titles WHERE id = ?",
                      (title_id,)).fetchone()
    if row is None or row["collection_id"] is not None:
        return ""
    if (row["kind"] or "") not in boxsets.SERIES_KINDS:
        return ""
    if not force and row["series_at"]:
        return ""
    try:
        found = series_of(row["imdb_id"], qid=qid,
                          year=row["release_year"], name=row["name"],
                          kind=row["kind"] or "movie")
    except Throttle:
        raise
    except Exception:
        return ""
    con.execute("UPDATE titles SET series_at = datetime('now') WHERE id = ?",
                (title_id,))
    con.commit()
    if not found:
        return ""
    best = found[0]
    collection_id = boxsets.resolve(con, best["name"], row["kind"])
    if collection_id is None:
        return ""
    # keep the series item: the reverse lookup ("what is in this series that we
    # do not have") needs the id, and a name search could land elsewhere later
    boxsets.remember_series(con, collection_id, best.get("qid") or "")
    order = str(best.get("order") or "").strip()
    boxsets.link_title(con, title_id, collection_id,
                       int(order) if order.isdigit() else None)
    return best["name"]



def enwiki_title(qid):
    """English Wikipedia page title for a Wikidata item (for the REST summary)."""
    if not qid:
        return None
    params = urllib.parse.urlencode({
        "action": "wbgetentities", "format": "json", "props": "sitelinks",
        "sitefilter": "enwiki", "ids": qid,
    })
    try:
        payload = _get("https://www.wikidata.org/w/api.php?" + params)
    except Exception:
        return None
    entity = payload.get("entities", {}).get(qid, {})
    return (entity.get("sitelinks", {}).get("enwiki", {}) or {}).get("title")


def wikipedia_summary(page_title):
    """{overview, poster_url, description, year} from the Wikipedia REST summary."""
    url = ("https://en.wikipedia.org/api/rest_v1/page/summary/"
           + urllib.parse.quote(page_title.replace(" ", "_"), safe=""))
    data = _get(url, timeout=25)
    description = data.get("description") or ""
    match = YEAR.search(description) or YEAR.search(data.get("extract") or "")
    return {
        "overview": (data.get("extract") or "").strip() or None,
        "poster_url": (data.get("originalimage") or data.get("thumbnail") or {}).get("source"),
        "description": description or None,
        "year": int(match.group(0)) if match else None,
        # "disambiguation" for a "may refer to" page - a caller that wanted an
        # article about one thing should refuse it rather than quote the list
        "page_type": data.get("type") or "",
    }


def series_page(qid):
    """A film series' own article: one paragraph of prose and the lead picture.

    A collection has nothing to read but what somebody typed, because a series
    is not an item a catalogue normally describes. Wikipedia is the one keyless
    place it does have prose (`collections.wikidata_id` is the series, and its
    `enwiki` sitelink names the article), so this is what "Refresh metadata" on a
    collection asks for. `{}` when the item has no English article, or when that
    article is a disambiguation page rather than the series.
    """
    title = enwiki_title(qid)
    if not title:
        return {}
    page = wikipedia_summary(title)
    if page.get("page_type") == "disambiguation":
        return {}
    page["title"] = title
    page["url"] = ("https://en.wikipedia.org/wiki/"
                   + urllib.parse.quote(title.replace(" ", "_")))
    return page


MUSIC_SPLIT = re.compile(r"\s+[—–]\s+")
ALBUM_YEAR = re.compile(r"\s*\((?:19|20)\d{2}\)\s*$")
MB_ROOT = "https://musicbrainz.org/ws/2"
MB_PAUSE = 1.1                      # MusicBrainz asks for at most one request/second


def split_artist_album(name):
    """"ABBA — Gold (1992)" -> ("ABBA", "Gold (1992)"); no separator -> album only."""
    text = (name or "").strip()
    for pattern in (MUSIC_SPLIT, re.compile(r"\s+-\s+")):
        match = pattern.search(text)
        if match and match.start() > 0:
            return text[:match.start()].strip(), text[match.end():].strip()
    return None, text


def cover_art(mbid, size=500, kind="release-group"):
    """Front cover from the Cover Art Archive, or None if there is none.

    kind is "release-group" for the picture an album is known by, or
    "release" for one edition's own - which is the only way a deluxe edition
    can show its cover instead of the standard one's.
    """
    try:
        payload = _get("https://coverartarchive.org/%s/%s" % (kind, mbid))
    except Exception:
        return None
    for image in payload.get("images") or []:
        if image.get("front"):
            thumbs = image.get("thumbnails") or {}
            url = (thumbs.get(str(size)) or thumbs.get("large")
                   or image.get("image"))
            # the site is served over HTTPS, so mixed-content images are blocked
            if url and url.startswith("http://"):
                url = "https://" + url[len("http://"):]
            return url
    return None


ITUNES_SEARCH = "https://itunes.apple.com/search"
COVER_ARCHIVE = "https://coverartarchive.org/release/%s/front-500"
COVER_LIMIT = 40


def _art_size(url, size=600):
    """iTunes serves every picture as .../100x100bb.jpg - ask for a bigger one."""
    return re.sub(r"/\d+x\d+bb\.", "/%dx%dbb." % (size, size), url or "")


def _cover_label(*parts):
    return " \u00b7 ".join(str(part) for part in parts if part)


def _itunes_covers(term, entity):
    """What one shop sells under a name - one request, no key needed."""
    params = urllib.parse.urlencode({"term": term, "entity": entity,
                                     "limit": "25"})
    try:
        payload = _get(ITUNES_SEARCH + "?" + params, timeout=20)
    except Exception:
        return []
    return payload.get("results") or []


def _musicbrainz_covers(album, artist, artist_mbid, notes):
    """One entry per edition MusicBrainz files, pictures fetched by the browser.

    The Cover Art Archive answers /release/<id>/front-500 with the picture
    itself (a redirect to archive.org), so listing a shelf of editions costs
    one search request here and nothing at all until a tile is drawn.
    """
    clauses = ['release:"%s"' % (album or "").replace('"', "")]
    if artist_mbid:
        clauses.append("arid:%s" % artist_mbid)
    elif artist:
        clauses.append('artist:"%s"' % artist.replace('"', ""))
    params = urllib.parse.urlencode({"query": " AND ".join(clauses),
                                     "fmt": "json", "limit": "25"})
    try:
        time.sleep(MB_PAUSE)
        payload = _get(MB_ROOT + "/release?" + params)
    except Throttle:
        notes.append("MusicBrainz is rate-limiting us, so the editions it "
                     "files are missing from this list - try again in a minute.")
        return []
    except Exception as exc:
        notes.append("MusicBrainz could not be asked: %s" % exc)
        return []
    found = []
    for release in payload.get("releases") or []:
        rid = release.get("id")
        if not rid:
            continue
        media = release.get("media") or [{}]
        found.append({
            "source": "MusicBrainz",
            "url": COVER_ARCHIVE % rid,
            "label": _cover_label(release.get("title"), release.get("date"),
                                  release.get("country"),
                                  (media[0] or {}).get("format"),
                                  release.get("disambiguation")),
        })
    return found


def _wikipedia_covers(words):
    """A poster per Wikipedia article a name finds.

    PageImages no longer answers anonymous callers, so each hit's REST summary
    is asked for its lead picture instead: one request per article, and only a
    handful of articles.
    """
    params = urllib.parse.urlencode({"q": words, "limit": "6"})
    try:
        payload = _get("https://en.wikipedia.org/w/rest.php/v1/search/page?"
                       + params)
    except Exception:
        return []
    found = []
    for page in (payload.get("pages") or [])[:6]:
        title = (page.get("title") or "").strip()
        if not title:
            continue
        try:
            summary = _get("https://en.wikipedia.org/api/rest_v1/page/summary/"
                           + urllib.parse.quote(title.replace(" ", "_")),
                           timeout=15)
        except Exception:
            continue
        image = ((summary.get("originalimage") or {}).get("source")
                 or (summary.get("thumbnail") or {}).get("source") or "")
        if not image:
            continue
        found.append({"source": "Wikipedia",
                      "url": image.split("?")[0],
                      "label": _cover_label(title, summary.get("description"))})
    return found


def cover_search(row, term="", artist_mbid=None):
    """Every cover the free sources hold for one title, and a note when none.

    Albums: the iTunes Search API, which sells one picture per edition, and
    MusicBrainz, whose editions the Cover Art Archive answers for one by one -
    the browser fetches those, so a search costs one request here. Films and
    seasons: Wikipedia, whose articles each carry a poster. Nothing is
    downloaded: a result is a url, drawn when the tile is.
    """
    kind = (row["kind"] or "movie").lower()
    artist = ""
    if "artist_name" in row.keys():
        artist = (row["artist_name"] or "").strip()
    album = (row["name"] or "").strip()
    words = ((term or "").strip()
             or " ".join(part for part in (artist, album) if part))
    if not words:
        return [], "Nothing to search for yet - give the title a name."
    found, notes = [], []
    if kind == "music":
        hits = _itunes_covers(words, "album")
        if artist:
            # a shop's search is fuzzy - another band's record of the same name
            # comes back too - so the act the title is filed under wins
            same = [hit for hit in hits
                    if artist.lower() in (hit.get("artistName") or "").lower()]
            if same:
                hits = same
        for hit in hits:
            url = _art_size(hit.get("artworkUrl100"))
            if not url:
                continue
            found.append({"source": "iTunes", "url": url,
                          "label": _cover_label(
                              hit.get("collectionName"),
                              (hit.get("releaseDate") or "")[:4],
                              hit.get("country"))})
        found.extend(_musicbrainz_covers(album, artist, artist_mbid, notes))
    if not found:
        found.extend(_wikipedia_covers(words))
    seen, covers = set(), []
    for cover in found:
        if cover["url"] not in seen:
            seen.add(cover["url"])
            covers.append(cover)
    if not covers:
        notes.append("No cover art found for \u201c%s\u201d." % words)
    return covers[:COVER_LIMIT], " ".join(notes)


def musicbrainz_artist_matches(name, limit=10):
    """Every artist a MusicBrainz name search finds, best first.

    The search is Lucene, not equality, so `artist:"Bush"` also returns *Kate
    Bush* - which is how this shelf filed the British band under her biography.
    The caller decides which hit is the act; this only reports what the search
    said, so the same list can back a "pick the right one" page.
    """
    if not name:
        return []
    params = urllib.parse.urlencode({
        "query": 'artist:"%s"' % name.replace('"', " "),
        "fmt": "json", "limit": limit})
    time.sleep(MB_PAUSE)
    payload = _get(MB_ROOT + "/artist?" + params)
    found = []
    for row in (payload.get("artists") or []):
        life = row.get("life-span") or {}
        begin = (life.get("begin") or "")[:4]
        end = (life.get("end") or "")[:4]
        found.append({
            "mbid": row.get("id") or "",
            "name": row.get("name") or "",
            "disambiguation": row.get("disambiguation") or "",
            "country": row.get("country") or "",
            "kind": row.get("type") or "",
            "years": ("%s-%s" % (begin, end)) if begin and end else begin,
            "score": row.get("score") or 0,
        })
    return found


def musicbrainz_artist(name):
    """MusicBrainz' best guess at an artist: id, country, start year, note.

    Used to fill in the artists table from the act's own name, so nothing
    depends on Jellyfin being present. Only a hit whose name is the act's own
    counts (case, punctuation and a leading "The" aside): `artist:"Bush"`
    returns Kate Bush first, and a wrong id is worse than none - it drags a
    stranger's biography, portrait and links onto the shelf.
    """
    hits = musicbrainz_artist_matches(name, limit=10)
    if not hits:
        return None
    wanted = _act_key(name)
    best, best_rank = None, 0
    for hit in hits:
        key = _act_key(hit["name"])
        if key == wanted:
            rank = 100
        elif key.startswith(wanted) or key.endswith(wanted):
            rank = 20
        else:
            rank = 0
        if rank > best_rank:
            best, best_rank = hit, rank
    if best is None:
        return None
    years = best["years"]
    return {
        "name": best["name"] or name,
        "mbid": best["mbid"],
        "country": best["country"] or None,
        "formed_year": int(years[:4]) if years[:4].isdigit() else None,
        "note": best["disambiguation"] or best["kind"] or None,
    }
def musicbrainz_lookup(artist, album, year=None, artist_mbid=None):
    """Best release-group for one album: mbid, year, cover, title, credit, types."""
    album = ALBUM_YEAR.sub("", album or "").strip() or (album or "")
    clauses = ['releasegroup:"%s"' % album.replace('"', "")]
    if artist_mbid:
        clauses.append("arid:%s" % artist_mbid)   # the artist, exactly
    elif artist:
        clauses.append('artistname:"%s"' % artist.replace('"', ""))
    params = urllib.parse.urlencode({"query": " AND ".join(clauses),
                                     "fmt": "json", "limit": "12"})
    time.sleep(MB_PAUSE)
    payload = _get(MB_ROOT + "/release-group?" + params)

    wanted = _key(album)
    best = None
    for group in payload.get("release-groups") or []:
        title_key = _key(group.get("title"))
        if not title_key:
            continue
        # "Gold (1992)" in the library is MusicBrainz's "Gold: Greatest Hits", so a
        # title that starts with the album name still counts when the year agrees.
        if title_key != wanted and not (len(wanted) >= 3 and (
                title_key.startswith(wanted) or wanted.startswith(title_key))):
            continue
        score = int(group.get("score") or 0)
        date = group.get("first-release-date") or ""
        credit_here = "".join(
            (part.get("name") or "") for part in group.get("artist-credit") or [])
        if artist and artist.lower() in credit_here.lower():
            score += 30
        if year and date[:4] == str(year):
            score += 40                     # the year breaks album/remaster ties
        elif year and date[:4]:
            score -= 25
        if best is None or score > best[0]:
            best = (score, group)
    if best is None:
        return None

    group = best[1]
    date = group.get("first-release-date") or ""
    credit = "".join((part.get("name") or "") + (part.get("joinphrase") or "")
                     for part in group.get("artist-credit") or [])
    types = [group.get("primary-type")] + list(group.get("secondary-types") or [])
    art = None
    if group.get("id"):
        # the Cover Art Archive shares MusicBrainz's one-request-per-second rule,
        # so the second request waits as well - back-to-back calls get 503s
        time.sleep(MB_PAUSE)
        art = cover_art(group.get("id"))
    return {
        "mbid": group.get("id"),
        "title": group.get("title") or album,
        "credit": credit.strip() or (artist or ""),
        "date": date,
        "year": int(date[:4]) if date[:4].isdigit() else None,
        "types": " · ".join(part for part in types if part),
        "cover": art,
    }


def musicbrainz_release(mbid):
    """One album by its stored MusicBrainz id, in the shape lookup returns.

    The id may be a release or a release group - both are tried, because the
    catalogue has historically stored whichever the lookup found first.
    """
    group = None
    release_id = None
    try:
        group = _get("%s/release-group/%s?inc=artist-credits&fmt=json"
                     % (MB_ROOT, mbid))
    except Exception:
        group = None
    if group and not group.get("error"):
        date = group.get("first-release-date") or ""
        title = group.get("title") or ""
        types = [group.get("primary-type")] + list(
            group.get("secondary-types") or [])
        credit = "".join(
            (part.get("name") or "") + (part.get("joinphrase") or "")
            for part in group.get("artist-credit") or [])
        art_id = group.get("id") or mbid
    else:
        release = _get(
            "%s/release/%s?inc=artist-credits+release-groups&fmt=json"
            % (MB_ROOT, mbid))
        if not release or release.get("error"):
            return None
        group = release.get("release-group") or {}
        date = release.get("date") or group.get("first-release-date") or ""
        title = release.get("title") or group.get("title") or ""
        types = [group.get("primary-type")] + list(
            group.get("secondary-types") or [])
        credit = "".join(
            (part.get("name") or "") + (part.get("joinphrase") or "")
            for part in release.get("artist-credit") or [])
        art_id = group.get("id") or mbid
        release_id = release.get("id") or mbid
    time.sleep(MB_PAUSE)
    cover = None
    if release_id:          # one edition: its own picture, not the group's
        cover = cover_art(release_id, kind="release")
        time.sleep(MB_PAUSE)
    if not cover:
        cover = cover_art(art_id)
    return {
        "mbid": mbid,
        "title": title,
        "credit": credit.strip(),
        "date": date,
        "year": int(date[:4]) if date[:4].isdigit() else None,
        "types": " \u00b7 ".join(part for part in types if part),
        "cover": cover,
    }


def refresh_music(con, row):
    """MusicBrainz for one album - cover art, first release and the MB link."""
    artist, album = split_artist_album(row["name"])
    artist_mbid = None
    if row["artist_id"]:
        looked = con.execute(
            "SELECT name, musicbrainz_id FROM artists WHERE id = ?",
            (row["artist_id"],)).fetchone()
        if looked:
            artist = artist or looked["name"]
            artist_mbid = (looked["musicbrainz_id"] or "").strip()    \
                or None
    found = None
    # an album that already carries a release-group id is looked up by that id:
    # its name is the album alone now, which no search can match on
    if row["musicbrainz_id"]:
        try:
            found = musicbrainz_release(row["musicbrainz_id"])
        except Throttle:
            return "rate-limited"
        except Exception:
            found = None
    if found is None:
        try:
            found = musicbrainz_lookup(artist, album,
                                       row["release_year"], artist_mbid)
        except Throttle:
            return "rate-limited"
        except Exception:
            found = None
    if not found:
        return "no MusicBrainz match for %s" % album

    updates = {"musicbrainz_id": found["mbid"]}
    if found["year"]:
        updates["release_year"] = found["year"]
    if found["cover"]:
        # every cover a lookup offers is kept, so the pencil can switch
        # between editions; but a cover the household picked stays
        remember_art(con, row["id"], "musicbrainz", found["cover"])
        if not ((row["art_source"] or "") == "poster"
                and (row["poster_url"] or "")):
            updates["poster_url"] = found["cover"]
    words = ["%s by %s" % (found["title"], found["credit"]) if found["credit"]
             else found["title"]]
    if found["date"]:
        words.append("first released %s" % found["date"])
    if found["types"]:
        words.append(found["types"])
    updates["overview"] = "%s." % ", ".join(words)

    columns = ", ".join("%s = ?" % key for key in updates)
    con.execute("UPDATE titles SET %s, metadata_source = 'musicbrainz',"
                " metadata_updated_at = datetime('now') WHERE id = ?" % columns,
                list(updates.values()) + [row["id"]])
    con.commit()

    message = "metadata refreshed from musicbrainz"
    if not found["cover"]:
        message += " (no cover art on file)"
    return message


def refresh(con, title_id):
    """Pull metadata for one title and store it - never raises.

    Two request budget: Wikipedia first (page search + summary), and Wikidata only
    when an IMDb id has to be honoured or the Wikipedia page is missing.
    """
    row = con.execute("SELECT * FROM titles WHERE id = ?", (title_id,)).fetchone()
    if row is None:
        return "title not found"

    if row["kind"] == "music":
        return refresh_music(con, row)

    cleaned = clean_title(row["name"])
    updates = {}
    source = []
    unresolved = []
    qid = None          # the Wikidata item, when one was found

    try:
        if row["imdb_id"]:
            qid = qid_by_imdb(row["imdb_id"])
            if qid:
                source.append("imdb")
        if not qid and row["tmdb_id"]:
            qid = qid_by_tmdb(row["tmdb_id"], row["kind"])
            if qid:
                source.append("tmdb")
        if qid:
            year, imdb = fetch_entities([qid]).get(qid, (None, None))
            if year:
                updates["release_year"] = year          # the id is authoritative
            if imdb and imdb != row["imdb_id"]:
                updates["imdb_id"] = imdb
            page = enwiki_title(qid)
        else:
            page = None
            if row["imdb_id"]:
                unresolved.append("IMDb %s" % row["imdb_id"])
            if row["tmdb_id"]:
                unresolved.append("TMDB %s" % row["tmdb_id"])
        if not page:
            page = wikipedia_search(cleaned, year=row["release_year"],
                                    kind=row["kind"])
        if not page:
            qid = search_wikidata([cleaned], workers=1,
                                  kind=row["kind"]).get(cleaned)
            if qid:
                year, imdb = fetch_entities([qid]).get(qid, (None, None))
                if year and not row["release_year"]:
                    updates["release_year"] = year
                if imdb and not row["imdb_id"]:
                    updates["imdb_id"] = imdb
                page = enwiki_title(qid)
                source.append("wikidata")

        if page:
            summary = wikipedia_summary(page)
            updates["overview"] = summary["overview"]
            updates["poster_url"] = summary["poster_url"]
            if summary["year"] and "release_year" not in updates and not row["release_year"]:
                updates["release_year"] = summary["year"]
            source.append("wikipedia")
    except Throttle:
        return "rate-limited"
    except Exception:
        pass

    if not updates:
        return "no metadata found for %s" % cleaned

    updates["metadata_source"] = "+".join(source) or "manual"
    if updates.get("poster_url"):
        # keep every poster a lookup offers, so the shelf can switch between them
        remember_art(con, title_id, updates["metadata_source"], updates["poster_url"])
        kept = con.execute("SELECT art_source FROM titles WHERE id = ?",
                           (title_id,)).fetchone()
        if kept and kept["art_source"] == "poster":
            updates.pop("poster_url")      # a chosen poster stays chosen
    columns = ", ".join("%s = ?" % key for key in updates)
    con.execute("UPDATE titles SET %s, metadata_updated_at = datetime('now') WHERE id = ?"
                % columns, list(updates.values()) + [title_id])
    con.commit()
    message = "metadata refreshed from %s" % updates["metadata_source"]
    if unresolved:
        message = ("%s not found in Wikidata - matched by name and year instead. %s"
                   % (" and ".join(unresolved), message))
    # The film series Wikidata files it under, never over one picked by hand.
    try:
        filed = autofill_series(con, title_id, qid=qid)
    except Exception:
        filed = ""
    if filed:
        message += " - filed under %s" % filed
    return message



def musicbrainz_artist_by_id(mbid):
    """One act by its MusicBrainz id: name, country, the year it started."""
    found = _get("%s/artist/%s?fmt=json" % (MB_ROOT, mbid))
    if not found or found.get("error"):
        return None
    life = found.get("life-span") or {}
    begin = (life.get("begin") or "")[:4]
    return {
        "mbid": found.get("id") or mbid,
        "name": found.get("name") or "",
        "country": found.get("country"),
        "year": int(begin) if begin.isdigit() else None,
        "kind": (found.get("type") or "").lower(),
    }

UPC_ROOT = "https://api.upcitemdb.com/prod/trial/lookup"
UPC_SEARCH = "https://api.upcitemdb.com/prod/trial/search"


def musicbrainz_barcode(upc):
    """The album MusicBrainz files under this barcode, if any.

    A CD or LP barcode is a `release` field there, so one search answers
    "what is this" with the album, the artist, the year and the release id
    the music refresh already understands.
    """
    params = urllib.parse.urlencode({"query": "barcode:%s" % upc,
                                     "fmt": "json", "limit": 5})
    time.sleep(MB_PAUSE)
    payload = _get(MB_ROOT + "/release?" + params)
    for release in payload.get("releases") or []:
        # the search is fuzzy, so a hit only counts when the release really
        # carries this barcode - placeholder codes like 0000000000000 are
        # surprisingly common and would otherwise win every unknown scan
        if re.sub(r"\D", "", release.get("barcode") or "") != upc:
            continue
        credit = release.get("artist-credit") or []
        artist = "".join((part.get("name") or "") +
                         (part.get("joinphrase") or "")
                         for part in credit).strip()
        artist_mbid = ""
        for part in credit:
            artist_mbid = ((part.get("artist") or {}).get("id") or "")
            if artist_mbid:
                break
        date = release.get("date") or ""
        return {
            "source": "MusicBrainz",
            "kind": "music",
            "title": release.get("title") or "",
            "artist": artist,
            "artist_mbid": artist_mbid,
            "musicbrainz_id": release.get("id") or "",
            "release_group": ((release.get("release-group") or {})
                              .get("id") or ""),
            "date": date,
            "year": int(date[:4]) if date[:4].isdigit() else None,
        }
    return None


def upc_lookup(upc):
    """What a case barcode belongs to: MusicBrainz, then a product API.

    Returns (match, note). The note says which service let us down, so the page
    can say why it found nothing instead of filling nothing in silence.

    A 13-digit EAN that starts with a zero is the same product as the 12-digit
    UPC-A, and the two databases disagree about which form to file, so both are
    asked. UPCitemdb's trial `lookup` also misses a lot that its fuzzy `search`
    still finds, so that is the last try before giving up.
    """
    if len(set(upc)) == 1:
        return None, "That barcode is a placeholder, not a real one."
    codes = [upc]
    if len(upc) == 13 and upc.startswith("0"):
        codes.append(upc.lstrip("0"))
    elif len(upc) == 12:
        codes.append("0" + upc)

    notes = []
    for code in codes:
        try:
            match = musicbrainz_barcode(code)
        except Throttle:
            notes.append("MusicBrainz is rate-limiting us - try again shortly.")
            break
        except Exception as exc:                  # offline, blocked, bad reply
            notes.append("MusicBrainz: %s." % exc)
            break
        if match:
            return match, " ".join(notes)

    # A disc barcode: MusicBrainz has no discs and the product APIs have few,
    # but a shop that files releases by barcode does.
    shop, shop_note = shop_barcode(upc)
    if shop_note:
        notes.append(shop_note)
    if shop:
        return shop, " ".join(notes)

    for code in codes:
        try:
            payload = _get(UPC_ROOT + "?" +
                           urllib.parse.urlencode({"upc": code}))
        except urllib.error.HTTPError as exc:
            # a 400 is "no such product", not a failure
            notes.append("UPCitemdb has no product for %s." % code
                         if exc.code == 400 else
                         "UPCitemdb answered %s." % exc.code)
            continue
        except Exception as exc:
            notes.append("UPCitemdb: %s." % exc)
            payload = None
            break
        for item in payload.get("items") or []:
            if (item.get("title") or "").strip():
                return _upc_match(item), " ".join(notes)
        if payload.get("code") and payload.get("code") != "OK":
            notes.append("UPCitemdb said %s." % payload.get("code"))

    try:
        payload = _get(UPC_SEARCH + "?" + urllib.parse.urlencode({"s": upc}))
    except Exception:
        payload = None
    for item in (payload or {}).get("items") or []:
        if (item.get("title") or "").strip():
            notes.append("Fuzzy match - check the title.")
            return _upc_match(item), " ".join(notes)
    if not notes:
        notes.append("Neither MusicBrainz nor UPCitemdb knows that barcode.")
    return None, " ".join(notes)


def _upc_match(item):
    """One UPCitemdb item as the dict the add dialog understands."""
    return {
        "source": "UPCitemdb",
        "kind": "",
        "title": (item.get("title") or "").strip(),
        "artist": (item.get("brand") or "").strip(),
        "category": (item.get("category") or "").strip(),
        "year": None,
    }


# -- a shop that files discs by barcode --------------------------------------
#
# MusicBrainz has albums and the product APIs have groceries, so a Blu-ray case
# was the one barcode nobody could answer. imusic.com does answer it: their
# /movies/<upc> (and /music/<upc>) replies 301 to
# /movies/<upc>/<year>-<title>-<format>, so the title arrives in the Location
# header and no page has to be parsed or rendered.

SHOP_ROOT = "https://imusic.com"
SHOP_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
           "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
SHOP_FORMATS = ("4k-uhd", "3d-blu-ray", "blu-ray", "dvd", "uhd", "steelbook",
                "box-set", "boxset", "import", "reissue", "remastered")
_SHOP_SEEN = {}                      # barcode -> (match, note), this process only
_SHOP_LAST = [0.0]                   # when we last asked, so we stay a browser
SHOP_PAUSE = 60.0                    # one shop request a minute, no more
_SHOP_SMALL = {"the", "a", "an", "of", "and", "in", "on", "to", "for", "at",
               "by", "with", "from", "vs"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Leave a 301 alone: its Location is the answer."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _slug_words(slug):
    """(year, title, format) from a shop slug like 2024-madame-web-blu-ray."""
    year = None
    rest = slug
    match = re.match(r"^((?:19|20)\d\d)-(.+)$", slug)
    if match:
        year = int(match.group(1))
        rest = match.group(2)
    found = []
    changed = True
    while changed:
        changed = False
        for token in sorted(SHOP_FORMATS, key=len, reverse=True):
            if rest == token or rest.endswith("-" + token):
                rest = rest[:-len(token)].rstrip("-")
                found.insert(0, token)
                changed = True
                break
    words = [w for w in rest.split("-") if w]
    if not words:
        return year, "", " ".join(found)
    title = []
    for position, word in enumerate(words):
        if word.isupper() or position == 0 or word.lower() not in _SHOP_SMALL:
            title.append(word[:1].upper() + word[1:])
        else:
            title.append(word)
    return year, " ".join(title), " ".join(found)


def shop_barcode(upc):
    """(match, note) for a barcode a shop files under its own number.

    One request and no page parsed: imusic.com answers /movies/<upc> (or
    /music/<upc>) with a 301 whose Location ends in /<year>-<title>-<format>,
    so the title is in the header. It is the only free source that carries disc
    barcodes, so the scrape is worth it; their WAF answers 418 once it has had
    enough, which is reported as "busy" and not cached, rather than pretending
    the barcode does not exist.
    """
    if upc in _SHOP_SEEN:
        return _SHOP_SEEN[upc]
    # Stay a browser rather than a crawler: one request a minute, process-wide,
    # and silence after that - their WAF bans the whole household's address.
    now = time.time()
    if now - _SHOP_LAST[0] < SHOP_PAUSE:
        return None, ""
    _SHOP_LAST[0] = now
    opener = urllib.request.build_opener(_NoRedirect)
    for section, kind in (("movies", "movie"), ("music", "music")):
        request = urllib.request.Request(
            SHOP_ROOT + "/" + section + "/" + upc,
            headers={"User-Agent": SHOP_UA})
        location = ""
        try:
            with opener.open(request, timeout=10) as response:
                location = response.headers.get("Location") or ""
        except urllib.error.HTTPError as exc:
            if exc.code in (301, 302, 303, 307, 308):
                location = exc.headers.get("Location") or ""
            elif exc.code == 418:
                return None, ("The shop that files discs by barcode is "
                              "rate-limiting us - try again in a minute.")
            else:
                continue
        except Exception:
            return None, ""
        slug = location.rstrip("/").split("/")[-1]
        if not slug or slug.isdigit():
            continue
        year, title, fmt = _slug_words(slug)
        if not title:
            continue
        match = {"source": "imusic.com", "kind": kind, "title": title,
                 "artist": "", "category": fmt, "year": year}
        _SHOP_SEEN[upc] = (match, "")
        return match, ""
    _SHOP_SEEN[upc] = (None, "")
    return None, ""


# -- acts --------------------------------------------------------------------
#
# MusicBrainz knows an act's id, country and start year and little else: the
# only "biography" it offers is a disambiguation line ("Group"), which is what
# used to sit in artists.overview. A real biography, a portrait and somewhere
# to read more therefore come from Wikipedia.

MUSIC_ROLES = ("band", "group", "duo", "trio", "singer", "songwriter",
               "musician", "rapper", "composer", "record producer",
               "disc jockey", "guitarist", "drummer", "vocalist", "pianist",
               "bassist", "orchestra", "choir", "rock band", "pop group",
               "jazz", "hip hop", "heavy metal", "country music", "ensemble")


def _summary_or_empty(title):
    """wikipedia_summary, but a page that does not exist is just empty."""
    try:
        return wikipedia_summary(title) or {}
    except Exception:
        return {}


def _about_music(summary):
    """Is that article about an act, or about a place, a word, a film?"""
    text = ((summary.get("description") or "") + " "
            + (summary.get("overview") or "")[:400]).lower()
    if ("may refer to" in text or "can refer to" in text
            or "referred to by the same term" in text
            or "disambiguation" in text):
        return False
    return any(role in text for role in MUSIC_ROLES)


def wikipedia_artist_name(name):
    """The en.wikipedia article for an act: the plain title, else a search hit.

    Most acts have an article under their own name ("Bon Jovi", "Billy Joel"),
    so that is tried first, then the two usual disambiguated forms. Wikipedia's
    own search comes last and its hits are checked twice - the page title has
    to be the act's name, and the article has to read like it is about music.
    """
    wanted = _key(name)
    for title in (name, name + " (band)", name + " (musician)"):
        summary = _summary_or_empty(title)
        if (summary.get("overview") or "").strip() and _about_music(summary):
            return title
    try:
        data = _get("https://en.wikipedia.org/w/rest.php/v1/search/page?"
                    + urllib.parse.urlencode({"q": name, "limit": "6"}))
    except Exception:
        return None
    for page in (data or {}).get("pages", []):
        candidate = page.get("title") or ""
        if _key(candidate.split("(")[0].strip()) != wanted:
            continue
        summary = _summary_or_empty(candidate)
        if (summary.get("overview") or "").strip() and _about_music(summary):
            return candidate
    return None


def wikidata_qid(page_title):
    """The Wikidata item behind an en.wikipedia article, or None."""
    try:
        data = _get("https://en.wikipedia.org/w/api.php?action=query"
                    "&format=json&prop=pageprops&titles="
                    + urllib.parse.quote(page_title))
    except Exception:
        return None
    pages = ((data or {}).get("query") or {}).get("pages") or {}
    for page in pages.values():
        qid = (page.get("pageprops") or {}).get("wikibase_item")
        if qid:
            return qid
    return None


def musicbrainz_url_rels(mbid):
    """{relation type: [url, ...]} for an act: wikidata, discogs, allmusic...

    MusicBrainz keeps the "official" links out, and the wikidata one is the key
    to the right article - which is how "Air" stops resolving to the gas.
    """
    if not mbid:
        return {}
    try:
        data = _get("%s/artist/%s?inc=url-rels&fmt=json" % (MB_ROOT, mbid)) or {}
    except Exception:
        return {}
    time.sleep(MB_PAUSE)                    # MusicBrainz asks for one a second
    found = {}
    for rel in data.get("relations") or []:
        url = ((rel.get("url") or {}).get("resource") or "").strip()
        kind = (rel.get("type") or "").strip().lower()
        if url and kind:
            found.setdefault(kind, []).append(url)
    return found


def _qid_from_url(url):
    match = re.search(r"/(Q\d+)", url or "")
    return match.group(1) if match else None


def artist_bio(name, mbid=None):
    """{overview, image_url, wiki_url, links} for an act, or None.

    The MusicBrainz id is tried first: an act with an id has a Wikidata item,
    and that item names its English article - which is the difference between
    "Air" the gas and "Air (French band)". Only an act with no id (or no links
    on MusicBrainz) falls back to searching Wikipedia by name.

    `links` is where else the act can be read about: MusicBrainz keeps rels for
    Discogs, AllMusic, Last.fm, IMDb and the official site, and those are used
    when they exist instead of guessing at search pages.
    """
    rels = musicbrainz_url_rels(mbid)
    title = None
    qid = None
    for url in rels.get("wikidata", []):
        qid = _qid_from_url(url)
        if qid:
            title = enwiki_title(qid)
        if title:
            break
    if not title:
        for url in rels.get("wikipedia", []):
            title = urllib.parse.unquote(url.rsplit("/", 1)[-1]).replace("_", " ")
            break
    if title:                               # an id said so; it must have text
        summary = _summary_or_empty(title)
        if not (summary.get("overview") or "").strip():
            title = None
    if not title:
        title = wikipedia_artist_name(name)
    if not title:
        return None
    summary = _summary_or_empty(title)
    overview = (summary.get("overview") or "").strip()
    if not overview:
        return None
    article = ("https://en.wikipedia.org/wiki/"
               + urllib.parse.quote(title.replace(" ", "_")))
    if not qid:
        qid = wikidata_qid(title)
    links = [{"label": "Wikipedia", "url": article}]
    if qid:
        links.append({"label": "Wikidata",
                      "url": "https://www.wikidata.org/wiki/" + qid})
    if mbid:
        links.append({"label": "MusicBrainz",
                      "url": "https://musicbrainz.org/artist/" + mbid})
    for kind, label in (("discogs", "Discogs"), ("allmusic", "AllMusic"),
                        ("last.fm", "Last.fm"), ("imdb", "IMDb"),
                        ("official homepage", "Official site")):
        urls = rels.get(kind) or []
        if urls:
            links.append({"label": label, "url": urls[0]})
    if not rels.get("discogs"):
        links.append({"label": "Discogs",
                      "url": "https://www.discogs.com/search/?type=artist&q="
                             + urllib.parse.quote(name)})
    return {"overview": overview,
            "image_url": summary.get("poster_url"),
            "wiki_url": article,
            "links": links[:6]}


def _wiki_title(url):
    """The en.wikipedia page title inside an article url, or ""."""
    head = "https://en.wikipedia.org/wiki/"
    if not (url or "").startswith(head):
        return ""
    title = url[len(head):].split("#")[0].split("?")[0]
    return urllib.parse.unquote(title).replace("_", " ")


def _commons_file(file_name):
    """A Wikimedia Commons file as a url that serves a picture.

    Special:FilePath redirects to the file itself, and `width` asks Wikimedia
    for a rendered thumbnail - a 6 MB photograph has no business on a card.
    """
    name = (file_name or "").strip()
    if name.lower().startswith("file:"):
        name = name[5:].strip()
    if not name:
        return ""
    return ("https://commons.wikimedia.org/wiki/Special:FilePath/"
            + urllib.parse.quote(name.replace(" ", "_")) + "?width=500")


def artist_image(name, mbid=None, wiki_url=""):
    """A photograph of an act, or "": Wikipedia's lead image, then Wikidata.

    The article's own lead image is the best answer - it is the picture a
    reader of the biography sees - and an act whose article has none (or whose
    article nobody found) is asked of its Wikidata item's P18. `wiki_url` is
    the article the biography lookup already resolved, so a filled act costs
    one request rather than four.
    """
    title = _wiki_title(wiki_url)
    qid = ""
    if mbid:
        try:
            for url in (musicbrainz_url_rels(mbid).get("wikidata") or []):
                qid = _qid_from_url(url) or ""
                if qid:
                    break
        except Exception:
            qid = ""
    if not title:
        try:
            title = wikipedia_artist_name(name) or ""
        except Exception:
            title = ""
    if title:
        found = (_summary_or_empty(title).get("poster_url") or "").strip()
        if found:
            return found
    if not qid and title:
        qid = wikidata_qid(title) or ""
    if qid:
        entity = (_entities([qid]) or {}).get(qid) or {}
        for file_name in _claim_strings(entity, "P18"):
            picture = _commons_file(file_name)
            if picture:
                return picture
    return ""




def _search_pages(name, limit=6):
    """The en.wikipedia page titles a plain name search finds, best first."""
    params = urllib.parse.urlencode({"action": "query", "list": "search",
                                     "srsearch": name, "srlimit": limit,
                                     "format": "json"})
    data = _get("https://en.wikipedia.org/w/api.php?" + params)
    rows = ((data or {}).get("query") or {}).get("search") or []
    return [row.get("title") or "" for row in rows if row.get("title")]


def artist_cover_search(name, mbid="", wiki_url=""):
    """([{source, url, label}], note) - pictures of an act to choose between.

    The act's own article comes first (its lead image is the photograph a
    reader of the biography sees), then its Wikidata item's P18, then the lead
    images of the other Wikipedia pages a name search finds - which is how a
    band whose article carries no photograph gets one from a festival or an
    interview page. `note` is what is shown when nothing turns up.
    """
    found = []
    seen = set()

    def offer(source, url, label):
        url = (url or "").strip()
        if url and url not in seen:
            seen.add(url)
            found.append({"source": source, "url": url, "label": label})

    title = _wiki_title(wiki_url)
    if not title:
        try:
            title = wikipedia_artist_name(name) or ""
        except Exception:
            title = ""
    qid = ""
    if mbid:
        try:
            for url in (musicbrainz_url_rels(mbid).get("wikidata") or []):
                qid = _qid_from_url(url) or ""
                if qid:
                    break
        except Exception:
            qid = ""
    if title:
        offer("Wikipedia", _summary_or_empty(title).get("poster_url"), title)
    if not qid and title:
        qid = wikidata_qid(title) or ""
    if qid:
        entity = (_entities([qid]) or {}).get(qid) or {}
        for file_name in _claim_strings(entity, "P18"):
            offer("Wikidata", _commons_file(file_name), "the act on Wikidata")
    try:
        pages = _search_pages(name)
    except Exception:
        pages = []
    for page in pages:
        if page == title:
            continue
        summary = _summary_or_empty(page)
        if not _about_music(summary):
            continue
        offer("Wikipedia", summary.get("poster_url"), page)
    if not found:
        return [], ("No picture of %s was found by name - upload one, or paste "
                    "a link to an image." % name)
    return found, ""

def series_item_for(con, collection_id, row):
    """The Wikidata item for a collection, resolved through its own films.

    A series has an id nothing on the shelf stores, so it is found the way the
    films themselves were filed: ask Wikidata about one of them (IMDb id first,
    then by name) and keep the answer when it names *this* series. "" when no
    film here resolves to the collection's name - which is honest, because a
    collection can be a grouping nobody wrote down in Wikidata.
    """
    films = con.execute(
        "SELECT id, name, release_year, imdb_id FROM titles"
        " WHERE collection_id = ?"
        " ORDER BY (imdb_id IS NULL), release_year, id LIMIT 4",
        (collection_id,)).fetchall()
    for film in films:
        try:
            found = series_of(film["imdb_id"],
                                       year=film["release_year"],
                                       name=film["name"],
                                       kind=row["kind"] or "movie")
        except Throttle:
            raise
        except Exception:
            continue
        for item in found:
            if boxsets.same_name(item["name"], row["name"]):
                boxsets.remember_series(con, collection_id, item["qid"])
                return item["qid"]
    return ""


def refresh_collection(con, collection_id, row=None):
    """Everything a collection can learn from outside, in one call.

    The films of the series (so the page can say what is missing), the series'
    own Wikipedia paragraph and cover, and any film on the shelf that this
    series names but nobody has filed. The two buttons on a collection page,
    the admin's *Refresh metadata* and the nightly pass all come through here,
    so they cannot drift apart.

    Returns a summary: `qid` (the series' Wikidata item, "" when nobody knows
    one), `page` (Wikipedia's answer, {} with no article), `article` (that
    article's title), `members` (how many films the series lists), `filled`
    (the fields `fill_details()` filled) and `filed` (titles filed from the
    shelf). Raises `Throttle` when Wikimedia refuses, so the caller can wait
    or stop.
    """
    if row is None:
        row = boxsets.get(con, collection_id)
    summary = {"qid": "", "page": {}, "article": "", "members": 0,
               "filled": [], "filed": []}
    if row is None:
        return summary
    qid = (row["wikidata_id"] or "").strip()
    if not qid:
        qid = series_item_for(con, collection_id, row)
    summary["qid"] = qid
    if not qid:
        return summary
    films = series_members(qid, kind=(row["kind"] or "movie"))
    boxsets.store_members(con, collection_id, films)
    summary["members"] = len(films)
    page = series_page(qid)
    summary["page"] = page
    summary["article"] = page.get("title") or ""
    summary["filled"] = boxsets.fill_details(
        con, collection_id, overview=page.get("overview") or "",
        poster=page.get("poster_url") or "")
    # The member list names the series' films, so a film the shelf holds and
    # has never filed can be filed now - nobody has to do it by hand.
    summary["filed"] = boxsets.file_members(con, collection_id)
    return summary
