"""Talk to the household Jellyfin server.

Two jobs:
  * match a catalogue copy to the Jellyfin item that holds the same file, so a title
    page can offer a Play button
  * ask a Jellyfin client (Apple TV, Kodi, web) to start playing that item

The server lives on the same box as Home Assistant, so paths in Jellyfin
(`/media/QNAP/...`) are the same paths this add-on sees. Music albums and TV seasons
are matched by folder, films by name + year. Only the standard library is used.
"""
import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

OPTIONS_FILE = "/data/options.json"
DEFAULT_URL = "http://192.168.234.3:8096"
PAGE = 500
TIMEOUT = 120
_users = {}


class JellyfinError(Exception):
    """Anything that stops us talking to the server."""


def config(con=None):
    """(base_url, api_key): environment, then add-on options, then the settings table."""
    url = (os.environ.get("JELLYFIN_URL") or "").strip()
    key = (os.environ.get("JELLYFIN_API_KEY") or "").strip()
    if not (url and key):
        try:
            with open(OPTIONS_FILE, encoding="utf-8") as fh:
                options = json.load(fh)
        except (OSError, ValueError):
            options = {}
        url = url or str(options.get("jellyfin_url") or "").strip()
        key = key or str(options.get("jellyfin_api_key") or "").strip()
    if con is not None:
        rows = dict(con.execute("SELECT key, value FROM settings"
                                " WHERE key IN ('jellyfin_url', 'jellyfin_api_key')"))
        url = url or (rows.get("jellyfin_url") or "")
        key = key or (rows.get("jellyfin_api_key") or "")
    return (url or DEFAULT_URL).rstrip("/"), key


def servers(con):
    """Every configured Jellyfin server; an empty list means none."""
    return [dict(row) for row in con.execute(
        "SELECT id, label, url, api_key FROM jellyfin_servers"
        " WHERE is_active = 1 ORDER BY id")]


def _call(base, key, path, params=None, method="GET", payload=None, timeout=TIMEOUT):
    if not key:
        raise JellyfinError("No Jellyfin API key yet - add one in the add-on options")
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", 'MediaBrowser Token="%s"' % key)
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace").strip()[:200]
        raise JellyfinError("%s %s -> %s %s" % (method, path, exc.code, detail))
    except Exception as exc:
        raise JellyfinError("Could not reach Jellyfin at %s (%s)" % (base, exc))
    return json.loads(body) if body.strip() else {}


def _walk(base, key, include, fields="Path", user_id=""):
    """Every item of `include`, with the extra `fields` filled in.

    A user id matters: without one Jellyfin lists a subset (657 films instead of 885),
    which would leave whole libraries unmatched.
    """
    start = 0
    while True:
        params = {"Recursive": "true", "IncludeItemTypes": include, "Fields": fields,
                  "EnableImages": "false", "EnableUserData": "false",
                  "StartIndex": start, "Limit": PAGE}
        if user_id:
            params["UserId"] = user_id
        data = _call(base, key, "/Items", params)
        items = data.get("Items") or []
        for item in items:
            yield item
        start += PAGE
        if not items or start >= int(data.get("TotalRecordCount") or 0):
            break


def _user_id(base, key):
    """An administrator id to list the library as - Jellyfin hides items without one."""
    cached = _users.get(base)
    if cached:
        return cached
    try:
        users = _call(base, key, "/Users", timeout=30) or []
    except JellyfinError:
        return ""
    chosen = ""
    for user in users:
        if (user.get("Policy") or {}).get("IsAdministrator"):
            chosen = user.get("Id") or ""
            break
    if not chosen and users:
        chosen = users[0].get("Id") or ""
    _users[base] = chosen
    return chosen



def _norm(text):
    """Loose key: accents, punctuation and a leading 'the ' do not matter."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    text = re.sub(r"\s+", " ", text).strip()
    return text[4:] if text.startswith("the ") else text


def _key(path):
    return (path or "").replace("\\", "/").rstrip("/")


def _year(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0



def _index(base, key):
    """Lookup tables built from what Jellyfin already knows about."""
    by_path, by_dir, by_name, details = {}, {}, {}, {}
    counts = {"movie": 0, "episode": 0, "audio": 0, "album": 0, "season": 0}
    user_id = _user_id(base, key)

    for item in _walk(base, key, "Movie", "Path,ProductionYear", user_id):
        jf_id, path = item.get("Id"), _key(item.get("Path"))
        counts["movie"] += 1
        details[jf_id] = (item.get("Name") or "", path)
        if path:
            by_path[path] = (jf_id, "movie")
        name = _norm(item.get("Name"))
        movie_year = _year(item.get("ProductionYear"))
        by_name.setdefault(("movie", name, movie_year), (jf_id, "movie"))
        by_name.setdefault(("movie", name, 0), (jf_id, "movie"))
        compact = name.replace(" ", "")
        by_name.setdefault(("movie", compact, movie_year), (jf_id, "movie"))
        by_name.setdefault(("movie", compact, 0), (jf_id, "movie"))

    for item in _walk(base, key, "Episode", "Path,SeasonId,SeriesId", user_id):
        jf_id, path = item.get("Id"), _key(item.get("Path"))
        counts["episode"] += 1
        details.setdefault(jf_id, (item.get("SeriesName") or item.get("Name") or "", path))
        season, series = item.get("SeasonId"), item.get("SeriesId")
        if path and season:
            by_dir.setdefault(os.path.dirname(path), (season, "season"))
            details.setdefault(season, ("%s — season" % (item.get("SeriesName") or ""), ""))
        if path and series:
            by_dir.setdefault(os.path.dirname(os.path.dirname(path)), (series, "series"))
            details.setdefault(series, (item.get("SeriesName") or "", ""))

    for item in _walk(base, key, "Audio", "Path,AlbumId", user_id):
        path = _key(item.get("Path"))
        counts["audio"] += 1
        album = item.get("AlbumId")
        if path and album:
            by_dir.setdefault(os.path.dirname(path), (album, "album"))

    for item in _walk(base, key, "MusicAlbum", "AlbumArtist,Path", user_id):
        jf_id = item.get("Id")
        counts["album"] += 1
        artist = item.get("AlbumArtist") or ""
        label = "%s — %s" % (artist, item.get("Name")) if artist else (item.get("Name") or "")
        details.setdefault(jf_id, (label, _key(item.get("Path"))))
        by_name.setdefault(("album", _norm(artist), _norm(item.get("Name"))), (jf_id, "album"))
        by_name.setdefault(("album", "", _norm(item.get("Name"))), (jf_id, "album"))

    for item in _walk(base, key, "Season", "SeriesName,IndexNumber", user_id):
        jf_id = item.get("Id")
        counts["season"] += 1
        series = item.get("SeriesName") or ""
        details[jf_id] = ("%s — %s" % (series, item.get("Name")), "")
        by_name.setdefault(("season", _norm(series), _year(item.get("IndexNumber"))),
                           (jf_id, "season"))

    return {"by_path": by_path, "by_dir": by_dir, "by_name": by_name,
            "details": details, "counts": counts}


def _name_keys(name):
    """Loose spellings of a name, in the order we trust them.

    '10,000 B.C.' and Jellyfin's '10,000 BC' differ only in punctuation, and a
    catalogue that keeps a subtitle ('Tintin: Secret of the Unicorn') should
    still find the plain name.
    """
    keys = []
    for text in (name, (name or "").split(":")[0]):
        folded = _norm(text)
        if not folded:
            continue
        for key in (folded, folded.replace(" ", "")):
            if key not in keys:
                keys.append(key)
    return keys


def _name_match(index, name, year, kind):
    """A Jellyfin item for a title we could not match by folder."""
    by_name = index["by_name"]
    if kind == "movie":
        for key in _name_keys(name):
            found = (by_name.get(("movie", key, _year(year)))
                     or by_name.get(("movie", key, 0)))
            if found:
                return found
        return None
    if kind == "music":
        artist, _, album = (name or "").partition(" — ")
        if album:
            found = by_name.get(("album", _norm(artist), _norm(album)))
            if found:
                return found
        return by_name.get(("album", "", _norm(name)))
    season = re.match(r"^(.*) — Season (\d+)", name or "")
    if season:
        return by_name.get(("season", _norm(season.group(1)), _year(season.group(2))))


def sync_links(con, log=print, server=None):
    """Rebuild jellyfin_links from every server, or from the one given."""
    if server is None:
        found = servers(con)
        if not found:
            log("jellyfin: no server configured")
            return {"copies": 0, "unknown": 0}
        totals = {}
        for one in found:
            part = sync_links(con, log=log, server=one)
            copies = materialise(con, one, log=log)
            expand_versions(con, one, log=log)
            try:
                more = editions(con, one, log=log)
                if more:
                    log("jellyfin[%s]: %d further editions"
                        % (one["label"], more))
            except Exception as exc:
                log("jellyfin[%s]: editions skipped (%s)"
                    % (one["label"], exc))
            if copies:
                log("jellyfin[%s]: %d copies added" % (one["label"], copies))
            # whatever was just linked belongs to the server we just read
            con.execute("UPDATE jellyfin_links SET server_id = ?"
                        " WHERE server_id IS NULL", (one["id"],))
            # editions() above can attach a second library's item again,
            # so the dedupe has to be the last word
            dedupe_versions(con, one, log=log)
            con.commit()
            log("jellyfin[%s]: linked %d copies, %d still unknown"
                % (one["label"], part.get("copies", 0),
                   part.get("unknown", 0)))
            for key_, value in part.items():
                totals[key_] = totals.get(key_, 0) + value
        return totals

    """Rebuild jellyfin_links from the server: one playable id per catalogue copy."""
    base, key = server["url"], server.get("api_key") or ""
    if not key:
        raise JellyfinError("No Jellyfin API key yet - add one in the add-on options")
    log("jellyfin: reading the library from %s" % base)
    index = _index(base, key)
    log("jellyfin: saw %s" % index["counts"])

    rows = con.execute(
        "SELECT i.id AS item_id, t.name, t.release_year, t.kind,"
        "       r.path AS root_path, d.path AS rel_path"
        "  FROM items i"
        "  JOIN titles t ON t.id = i.title_id"
        "  LEFT JOIN digital_sources d ON d.item_id = i.id AND d.provider = 'rip'"
        "  LEFT JOIN media_roots r ON r.id = d.root_id").fetchall()

    stats = {"copies": 0, "by_path": 0, "by_name": 0, "unmatched": 0}
    con.execute("DELETE FROM jellyfin_links")
    for row in rows:
        stats["copies"] += 1
        found, matched_by = None, "path"
        if row["root_path"] and row["rel_path"]:
            absolute = _key(os.path.join(row["root_path"], row["rel_path"]))
            found = index["by_path"].get(absolute) or index["by_dir"].get(absolute)
        if not found:
            matched_by = "name"
            found = _name_match(index, row["name"], row["release_year"], row["kind"])
        if not found:
            stats["unmatched"] += 1
            continue
        stats["by_" + matched_by] += 1
        jf_id, jf_kind = found
        jf_name, jf_path = index["details"].get(jf_id, ("", ""))
        con.execute(
            "INSERT OR REPLACE INTO jellyfin_links"
            " (item_id, jf_id, jf_kind, jf_name, jf_path, matched_by,"
            "  server_id, synced_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (row["item_id"], jf_id, jf_kind, jf_name, jf_path,
             matched_by, server["id"]))
    con.commit()
    log("jellyfin: %(copies)d copies, %(by_path)d by path, %(by_name)d by name,"
        " %(unmatched)d unmatched" % stats)
    return stats


def materialise(con, server, log=print):
    """Give every linked library item a copy of its own.

    A Jellyfin copy is a discrete copy, not a note on a file copy, so the
    app still works when Jellyfin is the only playback source. The route
    records the server, the library id and the server's name.
    """
    owner = con.execute("SELECT id FROM users WHERE username = 'rips'"
                        ).fetchone()
    if owner is None:
        return 0
    made = 0
    rows = con.execute(
        "SELECT l.jf_id, i.title_id FROM jellyfin_links l"
        "  JOIN items i ON i.id = l.item_id"
        " WHERE l.server_id = ?", (server["id"],)).fetchall()
    for row in rows:
        if con.execute(
                "SELECT 1 FROM digital_sources WHERE provider = 'jellyfin'"
                "   AND server_id = ? AND external_id = ?",
                (server["id"], row["jf_id"])).fetchone():
            continue
        new_id = con.execute(
            "INSERT INTO items (title_id, owner_id, format, visibility,"
            " added_by) VALUES (?, ?, 'ripped', 'shared', ?)",
            (row["title_id"], owner["id"], owner["id"])).lastrowid
        con.execute(
            "INSERT INTO digital_sources (item_id, provider, server_id,"
            " external_id, notes) VALUES (?, 'jellyfin', ?, ?, ?)",
            (new_id, server["id"], row["jf_id"], server["label"]))
        made += 1
    con.commit()
    return made


def editions(con, server, log=print, dry_run=False):
    """Attach the other editions of a title, not just the first one found.

    The linking pass keeps one library item per name, so a film with a
    theatrical and an extended cut arrives as a single copy. This walks the
    library again and gives each further edition a copy of its own. Nothing
    is created for a title that has no Jellyfin copy already, so the
    catalogue still grows only from what the household owns.
    """
    base, key = server["url"], server.get("api_key") or ""
    index = _index(base, key)
    details = index["details"]
    owner = con.execute("SELECT id FROM users WHERE username = 'rips'"
                        ).fetchone()
    if owner is None:
        return 0
    routed = {row["external_id"] for row in con.execute(
        "SELECT external_id FROM digital_sources WHERE provider = 'jellyfin'"
        "   AND server_id = ?", (server["id"],))}
    titles = con.execute(
        "SELECT DISTINCT t.id, t.name FROM titles t"
        "  JOIN items i ON i.title_id = t.id"
        "  JOIN digital_sources d ON d.item_id = i.id"
        " WHERE d.provider = 'jellyfin' AND d.server_id = ?",
        (server["id"],)).fetchall()
    made = 0
    for title in titles:
        want = _norm(title["name"])
        if not want:
            continue
        for jf_id, info in details.items():
            have = _norm(info[0]) if info else ""
            if not have or jf_id in routed:
                continue
            # the same film, or a named edition of it -- not a sequel
            if not (have == want or have.startswith(want + " (")
                    or have.startswith(want + " - ")):
                continue
            log("edition: %s <- %s" % (title["name"], info[0]))
            if dry_run:
                routed.add(jf_id)
                continue
            new_id = con.execute(
                "INSERT INTO items (title_id, owner_id, format, visibility,"
                " added_by) VALUES (?, ?, 'ripped', 'shared', ?)",
                (title["id"], owner["id"], owner["id"])).lastrowid
            con.execute(
                "INSERT INTO digital_sources (item_id, provider, server_id,"
                " external_id, notes) VALUES (?, 'jellyfin', ?, ?, ?)",
                (new_id, server["id"], jf_id, server["label"]))
            routed.add(jf_id)
            made += 1
    con.commit()
    return made


def forget_server(con, server_id):
    """Remove a server: its links, its routes, and any copy only it backed."""
    touched = [row["item_id"] for row in con.execute(
        "SELECT item_id FROM digital_sources WHERE provider = 'jellyfin'"
        " AND server_id = ?", (server_id,))]
    con.execute("DELETE FROM jellyfin_links WHERE server_id = ?", (server_id,))
    con.execute("DELETE FROM digital_sources WHERE provider = 'jellyfin'"
                " AND server_id = ?", (server_id,))
    gone = 0
    for item_id in touched:
        if not con.execute("SELECT 1 FROM digital_sources WHERE item_id = ?",
                           (item_id,)).fetchone():
            con.execute("DELETE FROM items WHERE id = ?", (item_id,))
            gone += 1
    con.execute("DELETE FROM jellyfin_servers WHERE id = ?", (server_id,))
    con.commit()
    import dedupe
    dedupe.stray_titles(con)
    return len(touched), gone


def sessions(con=None):
    """Jellyfin clients that let someone else start playback on them."""
    base, key = config(con)
    if not key:
        return []
    try:
        rows = _call(base, key, "/Sessions", timeout=15) or []
    except JellyfinError:
        return []
    found = []
    for row in rows:
        if not row.get("SupportsRemoteControl"):
            continue
        found.append({"id": row.get("Id") or "",
                      "name": row.get("DeviceName") or row.get("Client") or "Jellyfin client",
                      "client": row.get("Client") or ""})
    found.sort(key=lambda item: item["name"].lower())
    return found


def play(con, session_id, jf_id, media_source_id=None):
    """Start `jf_id` on a Jellyfin client. Returns the banner message."""
    base, key = config(con)
    if not session_id:
        raise JellyfinError("Pick a Jellyfin device first.")
    if not jf_id:
        raise JellyfinError("That copy is not linked to Jellyfin yet - "
                            "press Sync with Jellyfin in Admin.")
    params = {"PlayCommand": "PlayNow", "ItemIds": jf_id}
    if media_source_id:
        params["MediaSourceIds"] = [media_source_id]
    _call(base, key, "/Sessions/%s/Playing" % urllib.parse.quote(session_id),
          params=params, method="POST", timeout=20)
    return "Sent to Jellyfin"


def link_for_title(con, title_id):
    return con.execute(
        "SELECT l.* FROM jellyfin_links l JOIN items i ON i.id = l.item_id"
        " WHERE i.title_id = ?"
        " ORDER BY CASE l.jf_kind WHEN 'movie' THEN 0 WHEN 'album' THEN 0 ELSE 1 END, i.id"
        " LIMIT 1", (title_id,)).fetchone()


def status(con):
    base, key = config(con)
    kinds = dict(con.execute("SELECT jf_kind, COUNT(*) FROM jellyfin_links"
                             " GROUP BY jf_kind").fetchall())
    return {"url": base,
            "configured": bool(key),
            "linked": con.execute("SELECT COUNT(*) FROM jellyfin_links").fetchone()[0],
            "copies": con.execute("SELECT COUNT(*) FROM items").fetchone()[0],
            "kinds": kinds,
            "synced_at": con.execute("SELECT MAX(synced_at) FROM jellyfin_links").fetchone()[0]}



def image(con, jf_id, size=600):
    """The cover Jellyfin holds for an item, cached under /data.

    Jellyfin already scraped a proper front-of-case image for everything it
    indexed, which beats the posters the catalogue picked up from Wikipedia.
    """
    if not jf_id or not re.match(r"^[A-Za-z0-9]+$", jf_id):
        raise JellyfinError("No such Jellyfin item.")
    base, key = config(con)
    if not key:
        raise JellyfinError("No Jellyfin API key yet.")
    folder = "/data/art"
    cached = os.path.join(folder, "%s-%d.jpg" % (jf_id, size))
    if os.path.isfile(cached):
        with open(cached, "rb") as handle:
            return handle.read()
    request = urllib.request.Request(
        "%s/Items/%s/Images/Primary?maxHeight=%d&quality=90"
        % (base, jf_id, size))
    request.add_header("Authorization", 'MediaBrowser Token="%s"' % key)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        raise JellyfinError("Jellyfin has no cover for that item (%s)." % exc.code)
    except Exception as exc:
        raise JellyfinError("Could not reach Jellyfin: %s" % exc)
    try:
        os.makedirs(folder, exist_ok=True)
        with open(cached, "wb") as handle:
            handle.write(data)
    except OSError:
        pass                                   # a read-only /data still serves it
    return data


_LOCK = threading.Lock()
STATE = {"running": False, "counts": {}, "started": None, "finished": None}


def sync_status():
    return dict(STATE)


def start_sync(db_path):
    """Refresh the links in the background, so new rips just have a Play button."""
    with _LOCK:
        if STATE["running"]:
            return False
        STATE.update({"running": True, "counts": {}, "finished": None,
                      "started": time.strftime("%Y-%m-%d %H:%M:%S")})
    threading.Thread(target=_run_sync, args=(db_path,), daemon=True).start()
    return True


def _run_sync(db_path):
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        if not config(con)[1]:
            counts = {}                     # no key yet: nothing to refresh
        else:
            counts = sync_links(con, log=lambda message: None)
            # every link belongs to a server: one row today, more later
            con.execute("UPDATE jellyfin_links SET server_id ="
                        " (SELECT MIN(id) FROM jellyfin_servers)"
                        " WHERE server_id IS NULL")
            con.commit()
            print("jellyfin: linked %(copies)d copies, %(unmatched)d still unknown"
                  % counts)
    except Exception as exc:                      # never take the server down
        print("jellyfin: sync failed: %s" % exc)
        counts = {"error": str(exc)}
    finally:
        con.close()
    try:  # linking is the last thing to touch copies, so tidy what it minted
        import dedupe
        streamed, dropped = dedupe.after_links(db_path)
        if streamed or dropped:
            print("jellyfin: %d stream-only copy(s), %d empty copy(s) dropped"
                  % (streamed, dropped))
    except Exception as exc:
        print("jellyfin: tidy up skipped (%s)" % exc)
    with _LOCK:
        STATE.update({"running": False, "counts": counts,
                      "finished": time.strftime("%Y-%m-%d %H:%M:%S")})



def _media_sources(base, key, jf_id):
    """Every file held by one Jellyfin item."""
    import json
    import urllib.request
    # Jellyfin 10.9 dropped GET /Items/<id>; ask through the list route.
    url = ("%s/Items?Ids=%s&Fields=MediaSources&Limit=1&apiKey=%s"
           % (base, jf_id, key))
    with urllib.request.urlopen(url, timeout=30) as response:
        data = json.load(response)
    found = data.get("Items") or []
    if not found:
        return []
    return [one for one in (found[0].get("MediaSources") or [])
            if one.get("Id")]


def _version_of(name):
    """'Extended Edition (2000) - 2160p Dolby Vision'
    -> ('Extended Edition', '2160p Dolby Vision')."""
    import re
    head, _, tail = (name or "").partition(" - ")
    head = re.sub(r"\s*\(\d{4}\)\s*$", "", head.strip()).strip()
    return head, (tail.split(",")[0].strip() or tail.strip())


def expand_versions(con, server, log=print):
    """A Jellyfin item can hold several files: give each one its own copy."""
    try:
        base, key = server["url"].rstrip("/"), server["api_key"]
    except (KeyError, TypeError):
        base, key = config(con)
    rows = con.execute(
        "SELECT ds.id, ds.item_id, ds.external_id, ds.media_source_id"
        "  FROM digital_sources ds JOIN items i ON i.id = ds.item_id"
        " WHERE ds.provider = 'jellyfin' AND ds.server_id = ?"
        "   AND ds.external_id IS NOT NULL AND ds.external_id != ''",
        (server["id"],)).fetchall()
    mine = {}
    for row in rows:
        mine.setdefault(row["external_id"], []).append(row)
    made = 0
    for jf_id, have in mine.items():
        try:
            sources = _media_sources(base, key, jf_id)
        except Exception as exc:            # one bad id must not stop us
            log("jellyfin: versions for %s skipped (%s)"
                % (jf_id[:6], exc))
            continue
        if len(sources) < 2:
            continue
        loose = [row for row in have if not row["media_source_id"]]
        for source in sources:
            source_id = source.get("Id")
            name = (source.get("Name") or "").strip()
            edition, quality = _version_of(name)
            known = con.execute(
                "SELECT 1 FROM digital_sources WHERE provider = 'jellyfin'"
                "   AND external_id = ? AND media_source_id = ?",
                (jf_id, source_id)).fetchone()
            if known:
                continue
            if loose:
                row = loose.pop(0)
                con.execute(
                    "UPDATE digital_sources SET media_source_id = ?,"
                    "       media_source_name = ? WHERE id = ?",
                    (source_id, name, row["id"]))
                if edition:
                    con.execute(
                        "UPDATE items SET edition = ? WHERE id = ?"
                        "   AND (edition IS NULL OR edition = '')",
                        (edition, row["item_id"]))
                continue
            made += _copy_of(con, have[0], jf_id, source_id, name,
                             edition or quality)
    if made:
        log("jellyfin[%s]: %d further versions" % (server["id"], made))
    return made


def _copy_of(con, route, jf_id, source_id, name, edition):
    """Insert one more copy of the same title, tied to one media source."""
    item = dict(con.execute("SELECT * FROM items WHERE id = ?",
                            (route["item_id"],)).fetchone())
    item.pop("id", None)
    item["format"] = "ripped"
    if "rip_path" in item:
        item["rip_path"] = None
    if "edition" in item:
        item["edition"] = edition or None
    made = con.execute("INSERT INTO items (%s) VALUES (%s)"
                       % (", ".join(item), ", ".join("?" * len(item))),
                       list(item.values()))
    source = dict(con.execute("SELECT * FROM digital_sources WHERE id = ?",
                             (route["id"],)).fetchone())
    source.pop("id", None)
    source["item_id"] = made.lastrowid
    source["media_source_id"] = source_id
    source["media_source_name"] = name
    con.execute("INSERT INTO digital_sources (%s) VALUES (%s)"
                % (", ".join(source), ", ".join("?" * len(source))),
                list(source.values()))
    return 1



def dedupe_versions(con, server, log=print):
    """One film, one copy.

    A film can sit in two Jellyfin libraries, so Jellyfin reports two items for
    it and the sync would materialise both. Keep the item whose year matches
    the title - same film, or the right remake - and drop the other copies.
    Items that cannot be judged (no year, no match) are left alone.
    """
    try:
        base, key = server["url"].rstrip("/"), server["api_key"]
    except (KeyError, TypeError):
        base, key = config(con)
    titles = con.execute(
        "SELECT i.title_id, t.release_year FROM digital_sources ds"
        "  JOIN items i ON i.id = ds.item_id"
        "  JOIN titles t ON t.id = i.title_id"
        " WHERE ds.provider = 'jellyfin' AND ds.server_id = ?"
        " GROUP BY i.title_id HAVING count(DISTINCT ds.external_id) > 1",
        (server["id"],)).fetchall()
    info = {}
    dropped = 0
    for row in titles:
        routes = {}
        for route in con.execute(
                "SELECT ds.id, ds.item_id, ds.external_id"
                "  FROM digital_sources ds JOIN items i ON i.id = ds.item_id"
                " WHERE i.title_id = ? AND ds.provider = 'jellyfin'"
                "   AND ds.server_id = ? ORDER BY ds.id",
                (row["title_id"], server["id"])):
            routes.setdefault(route["external_id"], []).append(route)
        for jf_id in routes:
            if jf_id in info:
                continue
            try:
                info[jf_id] = _item_year(base, key, jf_id)
            except Exception as exc:        # a stale id must not stop the run
                log("jellyfin: %s unreadable (%s)" % (jf_id[:6], exc))
                info[jf_id] = None
        wanted = []
        for jf_id in routes:
            year = info[jf_id]
            if not year or not row["release_year"]:
                continue
            try:
                if abs(int(year) - int(row["release_year"])) <= 1:
                    wanted.append(jf_id)
            except (TypeError, ValueError):
                continue
        if not wanted and row["release_year"] is None:
            # no title year to go by, but both items report the same year:
            # that is one release held in two libraries
            years = set(info[jf_id] for jf_id in routes if info[jf_id])
            if len(years) == 1 and len(routes) > 1:
                wanted = list(routes)
        if not wanted:
            continue                        # nothing trustworthy to compare
        keep = wanted[0]
        for jf_id in routes:
            if jf_id == keep:
                continue
            for route in routes[jf_id]:
                if _drop_copy(con, route["item_id"], route["id"]):
                    dropped += 1
    if dropped:
        log("jellyfin[%s]: %d duplicate copies dropped"
            % (server["id"], dropped))
    return dropped


def _item_year(base, key, jf_id):
    """The production year of one Jellyfin item, or None."""
    import json
    import urllib.request
    url = ("%s/Items?Ids=%s&Fields=ProductionYear&Limit=1&apiKey=%s"
           % (base, jf_id, key))
    with urllib.request.urlopen(url, timeout=30) as response:
        data = json.load(response)
    found = data.get("Items") or []
    return found[0].get("ProductionYear") if found else None


def _drop_copy(con, item_id, source_id):
    """Delete one stray route and report it.

    The route is always removed - it points at a film that is not this title,
    and a sync rebuilds a correct one. The copy itself only goes when it is a
    synthetic Jellyfin copy with nothing else left on it.
    """
    con.execute("DELETE FROM digital_sources WHERE id = ?", (source_id,))
    left = con.execute("SELECT count(*) FROM digital_sources WHERE item_id = ?",
                       (item_id,)).fetchone()[0]
    item = con.execute("SELECT format, rip_path FROM items WHERE id = ?",
                       (item_id,)).fetchone()
    synthetic = (item and item["format"] == "ripped" and not item["rip_path"])
    if not left and synthetic:
        con.execute("DELETE FROM jellyfin_links WHERE item_id = ?", (item_id,))
        con.execute("DELETE FROM items WHERE id = ?", (item_id,))
    return 1
