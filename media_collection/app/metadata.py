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


def wikipedia_search(name, limit=5, year=None):
    """Page title on en.wikipedia for "<name> film" (no Wikidata involved)."""
    params = urllib.parse.urlencode({"q": "%s film" % name, "limit": str(limit)})
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


def search_wikidata(names, limit=10, workers=6, pause=0.0):
    """{title: qid} - best film-ish item for each name, fetched concurrently."""
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
            if "film" not in description:
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


def musicbrainz_lookup(artist, album, year=None):
    """Best release-group for one album: mbid, year, cover, title, credit, types."""
    album = ALBUM_YEAR.sub("", album or "").strip() or (album or "")
    clauses = ['release:"%s"' % album.replace('"', "")]
    if artist:
        clauses.append('artist:"%s"' % artist.replace('"', ""))
    params = urllib.parse.urlencode({"query": " AND ".join(clauses),
                                     "fmt": "json", "limit": "8"})
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


def refresh_music(con, row):
    """MusicBrainz for one album - cover art, first release and the MB link."""
    artist, album = split_artist_album(row["name"])
    try:
        found = musicbrainz_lookup(artist, album, row["release_year"])
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
            page = wikipedia_search(cleaned, year=row["release_year"])
        if not page:
            qid = search_wikidata([cleaned], workers=1).get(cleaned)
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
    columns = ", ".join("%s = ?" % key for key in updates)
    con.execute("UPDATE titles SET %s, metadata_updated_at = datetime('now') WHERE id = ?"
                % columns, list(updates.values()) + [title_id])
    con.commit()
    message = "metadata refreshed from %s" % updates["metadata_source"]
    if unresolved:
        message = ("%s not found in Wikidata - matched by name and year instead. %s"
                   % (" and ".join(unresolved), message))
    return message
