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
    }


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


def cover_art(mbid, size=500):
    """Front cover from the Cover Art Archive, or None if the release has none."""
    try:
        payload = _get("https://coverartarchive.org/release-group/%s" % mbid)
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


def musicbrainz_artist(name):
    """MusicBrainz' best guess at an artist: id, country, year they formed.

    Used to fill in the artists table from the act's name alone, so nothing here
    depends on Jellyfin being present.
    """
    if not name:
        return None
    params = urllib.parse.urlencode({
        "query": 'artist:"%s"' % name.replace('"', ""),
        "fmt": "json", "limit": 1})
    time.sleep(MB_PAUSE)
    payload = _get(MB_ROOT + "/artist?" + params)
    found = (payload.get("artists") or [None])[0]
    if not found:
        return None
    life = found.get("life-span") or {}
    begin = life.get("begin") or ""
    return {
        "name": found.get("name") or name,
        "mbid": found.get("id"),
        "country": found.get("country"),
        "formed_year": int(begin[:4]) if begin[:4].isdigit() else None,
        "note": found.get("disambiguation") or found.get("type") or None,
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
    time.sleep(MB_PAUSE)
    return {
        "mbid": mbid,
        "title": title,
        "credit": credit.strip(),
        "date": date,
        "year": int(date[:4]) if date[:4].isdigit() else None,
        "types": " \u00b7 ".join(part for part in types if part),
        "cover": cover_art(art_id),
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

    try:
        qid = None
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
    """What a case barcode belongs to: MusicBrainz, then a product database.

    Returns (match, note). The note says which service let us down, so the
    page can be honest about a barcode it could not resolve instead of
    silently filling nothing.
    """
    if len(set(upc)) == 1:
        return None, "That barcode is a placeholder, not a real one."
    notes = []
    try:
        match = musicbrainz_barcode(upc)
    except Throttle:
        match = None
        notes.append("MusicBrainz is rate-limiting us - try again shortly.")
    except Exception as exc:                   # offline, blocked, unknown
        match = None
        notes.append("MusicBrainz: %s." % exc)
    if match:
        return match, " ".join(notes)
    try:
        payload = _get(UPC_ROOT + "?" +
                       urllib.parse.urlencode({"upc": upc}))
    except urllib.error.HTTPError as exc:
        # a 400 is "no such product", not a failure worth shouting about
        notes.append("UPCitemdb has no product for that code."
                     if exc.code == 400 else "UPCitemdb: %s." % exc)
        return None, " ".join(notes)
    except Exception as exc:
        notes.append("UPCitemdb: %s." % exc)
        return None, " ".join(notes)
    if payload.get("code") and payload.get("code") != "OK":
        notes.append("UPCitemdb said %s." % payload.get("code"))
    for item in payload.get("items") or []:
        title = (item.get("title") or "").strip()
        if not title:
            continue
        return {
            "source": "UPCitemdb",
            "kind": "",
            "title": title,
            "artist": (item.get("brand") or "").strip(),
            "category": (item.get("category") or "").strip(),
            "year": None,
        }, " ".join(notes)
    return None, " ".join(notes)


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
