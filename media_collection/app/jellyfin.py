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
DEFAULT_URL = ""  # a server is a row now; never guess an address
PAGE = 500
TIMEOUT = 120
_users = {}


class JellyfinError(Exception):
    """Anything that stops us talking to the server.

    `status` is the HTTP code when the server answered at all (0 when it did
    not), so a caller can tell "Jellyfin said no" from "we never got through".
    """

    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


def config(con=None):
    """(base_url, api_key) for the paths that talk to one server: play,
    sessions and artwork.

    Environment first, then the add-on options, then the server the admin
    page keeps (the lowest-numbered active one), then the old settings pair
    the single-server build wrote - so editing a server takes effect here.
    """
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
        first = con.execute("SELECT url, api_key FROM jellyfin_servers"
                            " WHERE is_active = 1 ORDER BY id").fetchone()
        if first:
            url = url or (first["url"] or "")
            key = key or (first["api_key"] or "")
    if con is not None and not (url and key):
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


def existing_ids(con, server_id, ids, chunk=50):
    """Which of these item ids the server still holds.

    Returns None when the question could not be put - the server row is
    missing or inactive, or the request failed - because an unreachable box
    must never look like an empty library. A JellyfinError raised here really
    did come from a request, so the caller can tell the two apart. An empty id
    (a route with no external_id) is never in the answer.
    """
    server = None
    for row in servers(con):
        if row["id"] == server_id:
            server = row
            break
    if server is None or not (server["url"] or "").strip():
        return None
    base = server["url"].rstrip("/")
    key = server["api_key"] or ""
    wanted = [jf_id for jf_id in ids if jf_id]
    found = set()
    for start in range(0, len(wanted), chunk):
        part = wanted[start:start + chunk]
        data = _call(base, key, "/Items", {
            "Ids": ",".join(part),
            "Recursive": "true",
            "Fields": "Path",
            "Limit": str(len(part)),
        })
        for item in (data.get("Items") or []):
            if item.get("Id"):
                found.add(item["Id"])
    return found


def _call(base, key, path, params=None, method="GET", payload=None, timeout=TIMEOUT):
    if not key:
        raise JellyfinError("No Jellyfin API key yet - add one in the add-on options")
    if not base:
        raise JellyfinError("No Jellyfin server yet - add one in Admin")
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
        raise JellyfinError("%s %s -> %s %s" % (method, path, exc.code, detail),
                            status=exc.code)
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


def _everywhere(base, key, include, fields="Path"):
    """Every item of a type, from both the user-scoped and the bare listing.

    The two `/Items` calls each withhold things the other returns: on this
    server the user-scoped walk found 886 movies against 658 without a user,
    but it also returned 0 of the 16 albums in the "Jeanine's Music" library,
    which the bare call lists - so a library that the admin account cannot see
    silently lost every route on it. Union both, deduped by id.
    """
    modes = [_user_id(base, key)]
    if modes[0]:
        modes.append("")
    seen = set()
    for user_id in modes:
        for item in _walk(base, key, include, fields, user_id):
            jf_id = item.get("Id")
            if jf_id in seen:
                continue
            seen.add(jf_id)
            yield item




def _norm(text):
    """Loose key: accents, punctuation and a leading 'the ' do not matter.

    A name made *only* of symbols does matter, though: "+", "×", "÷", "−" and
    "=" all folded to the empty string, so Ed Sheeran's symbol albums shared
    one key and the first one registered answered for the rest - a title named
    "+" was handed the "−" item and could not be linked to the right one.
    """
    source = unicodedata.normalize("NFKD", text or "")
    folded = source.encode("ascii", "ignore").decode()
    loose = re.sub(r"[^a-z0-9 ]+", " ", folded.lower())
    loose = re.sub(r"\s+", " ", loose).strip()
    if not loose:
        loose = re.sub(r"\s+", "", source).lower()
    return loose[4:] if loose.startswith("the ") else loose


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

    for item in _everywhere(base, key, "Movie", "Path,ProductionYear"):
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

    for item in _everywhere(base, key, "Episode", "Path,SeasonId,SeriesId"):
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

    for item in _everywhere(base, key, "Audio", "Path,AlbumId"):
        path = _key(item.get("Path"))
        counts["audio"] += 1
        album = item.get("AlbumId")
        if path and album:
            by_dir.setdefault(os.path.dirname(path), (album, "album"))

    for item in _everywhere(base, key, "MusicAlbum", "AlbumArtist,Path"):
        jf_id = item.get("Id")
        counts["album"] += 1
        artist = item.get("AlbumArtist") or ""
        label = "%s — %s" % (artist, item.get("Name")) if artist else (item.get("Name") or "")
        details.setdefault(jf_id, (label, _key(item.get("Path"))))
        by_name.setdefault(("album", _norm(artist), _norm(item.get("Name"))), (jf_id, "album"))
        bare = ("album", "", _norm(item.get("Name")))
        claimed = by_name.get(bare)
        if claimed is None:
            by_name[bare] = (jf_id, "album")
        elif claimed[0] != jf_id:
            by_name[bare] = None        # ambiguous: do not match on it

    for item in _everywhere(base, key, "Season", "SeriesName,IndexNumber"):
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


def _name_match(index, name, year, kind, artist=""):
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
        split_artist, _, album = (name or "").partition(" — ")
        wanted = album or name or ""
        for who in (artist, split_artist):
            if not who:
                continue
            found = by_name.get(("album", _norm(who), _norm(wanted)))
            if found:
                return found
        return by_name.get(("album", "", _norm(name)))
    season = re.match(r"^(.*) — Season (\d+)", name or "")
    if season:
        return by_name.get(("season", _norm(season.group(1)), _year(season.group(2))))


PROGRESS = {"running": False, "phase": "", "done": 0, "total": 0,
            "started": None, "finished": None}


def progress():
    """Live counters for the admin page while a sync runs."""
    return dict(PROGRESS)


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
    PROGRESS.update(running=True, phase="reading the library", done=0,
                    total=0, started=time.time(), finished=None)
    log("jellyfin: reading the library from %s" % base)
    index = _index(base, key)
    log("jellyfin: saw %s" % index["counts"])

    rows = con.execute(
        "SELECT i.id AS item_id, t.name, t.release_year, t.kind,"
        "       (SELECT a.name FROM artists a"
        "         WHERE a.id = t.artist_id) AS artist,"
        "       r.path AS root_path, d.path AS rel_path"
        "  FROM items i"
        "  JOIN titles t ON t.id = i.title_id"
        "  LEFT JOIN digital_sources d ON d.item_id = i.id AND d.provider = 'rip'"
        "  LEFT JOIN media_roots r ON r.id = d.root_id").fetchall()

    stats = {"copies": 0, "by_path": 0, "by_name": 0, "unmatched": 0}
    con.execute("DELETE FROM jellyfin_links")
    PROGRESS.update(phase="matching copies", total=len(rows), done=0)
    for row in rows:
        stats["copies"] += 1
        if stats["copies"] % 100 == 0:
            PROGRESS["done"] = stats["copies"]
        found, matched_by = None, "path"
        if row["root_path"] and row["rel_path"]:
            absolute = _key(os.path.join(row["root_path"], row["rel_path"]))
            found = index["by_path"].get(absolute) or index["by_dir"].get(absolute)
        if not found:
            matched_by = "name"
            found = _name_match(index, row["name"], row["release_year"],
                                row["kind"], row["artist"] or "")
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
    PROGRESS.update(running=False, phase="done",
                    done=stats["copies"], finished=time.time())
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
        found.append({
            "id": row.get("Id") or "",
            "name": _device_label(row),
            "client": row.get("Client") or "",
            "version": row.get("ApplicationVersion") or "",
            "user": row.get("UserName") or "",
            "device_id": row.get("DeviceId") or "",
            "ip": row.get("RemoteEndPoint") or "",
            "playing": bool(row.get("NowPlayingItem")),
            "at": row.get("LastActivityDate") or "",
        })
    # the device you used last is the one you probably want first
    found.sort(key=lambda item: (item["at"], item["name"]), reverse=True)
    return found


def _device_label(row):
    """How to name a Jellyfin client, from what the client says about itself.

    Jellyfin has no friendly device name of its own. `DeviceName` is whatever
    the app reported when it connected ("android TV", "Google Chromecast"),
    `Client` is the app ("Moonfin for Android TV") and `ApplicationVersion` its
    version - there is no room name, no model and no location anywhere in the
    API. So the label is built from those plus the Jellyfin user, which is what
    actually tells two identical boxes apart; the session's `RemoteEndPoint`
    tells them apart too. Renaming a device in Jellyfin's own Devices page sets
    a custom name that arrives here as `DeviceName`, and is the only way to get
    "Lounge TV" to appear.
    """
    device = (row.get("DeviceName") or "").strip()
    client = (row.get("Client") or "").strip()
    version = (row.get("ApplicationVersion") or "").strip()
    app = " ".join(part for part in (client, version) if part)
    if device and app and device.lower() != client.lower():
        return "%s (%s)" % (device, app)
    return app or device or "Jellyfin app"


def _session_row(base, key, session_id):
    """The `/Sessions` entry for one client, or {} when it has gone."""
    if not session_id:
        return {}
    try:
        rows = _call(base, key, "/Sessions", timeout=15) or []
    except JellyfinError:
        return {}
    for row in rows:
        if row.get("Id") == session_id:
            return row
    return {}


def access_reason(base, key, session_id, jf_id):
    """Why the session's user cannot see `jf_id`, or "" when it can.

    A play command is checked against the *session's* Jellyfin user, not our
    API key: the server accepts the command and the device then silently drops
    an item its user cannot see, which reached the page as "Sent to Jellyfin"
    with nothing playing. `GET /Users/<id>/Items/<item>` is the one listing
    question that honours a user's library access - `/Items?Ids=…` answers from
    the API key's view and calls an invisible item perfectly visible.

    Only a 404 counts as a refusal: a timeout or a 500 must never stop a play
    that would have worked, and a session carrying no user id cannot be asked.
    """
    session = _session_row(base, key, session_id)
    user_id = session.get("UserId") or ""
    if not user_id:
        return ""
    try:
        _call(base, key, "/Users/%s/Items/%s" % (user_id, jf_id), timeout=15)
        return ""
    except JellyfinError as exc:
        if exc.status != 404:
            return ""
    who = session.get("UserName") or "that Jellyfin user"
    return ("%s is signed in as %s, who cannot see this title - give that user "
            "access to the library in Jellyfin, or play it from a device that "
            "has it." % (_device_label(session), who))


def play(con, session_id, jf_id, media_source_id=None, kind=None):
    """Start `jf_id` on a Jellyfin client. Returns the banner message."""
    base, key = config(con)
    if not session_id:
        raise JellyfinError("Pick a Jellyfin device first.")
    if not jf_id:
        raise JellyfinError("That copy is not linked to Jellyfin yet - "
                            "press Sync with Jellyfin in Admin.")
    # the command is validated against the session's user, so a device whose
    # user cannot see the library must be refused here, not left silent
    why = access_reason(base, key, session_id, jf_id)
    if why:
        raise JellyfinError(why)
    # an album is one item holding tracks, and Jellyfin queues only the first
    # of them unless the tracks are named; a film keeps its single id
    item_ids = [jf_id]
    if kind == "music":
        tracks = album_tracks(base, key, jf_id)
        if tracks:
            item_ids = tracks
    params = {"PlayCommand": "PlayNow", "ItemIds": ",".join(item_ids)}
    if media_source_id:
        # urlencode() would send a Python list repr here, which Jellyfin
        # ignores - a plain string is what the query binder expects
        params["MediaSourceIds"] = media_source_id
    _call(base, key, "/Sessions/%s/Playing" % urllib.parse.quote(session_id),
          params=params, method="POST", timeout=20)
    return "Sent to Jellyfin"


def album_tracks(base, key, album_id):
    """The track ids of one album, in disc and track order.

    Playing an Album item starts only its first track on most clients, which is
    why an album left the queue after one song. Naming the tracks one by one is
    what makes the whole album actually play.
    """
    try:
        data = _call(base, key, "/Items", params={
            "ParentId": album_id, "Recursive": "true",
            "IncludeItemTypes": "Audio",
            "SortBy": "ParentIndexNumber,IndexNumber",
            "Limit": "300"})
    except JellyfinError:
        return []
    return [item.get("Id") for item in (data or {}).get("Items", [])
            if item.get("Id")]


def active_sessions(con=None):
    """Every Jellyfin client with something on, best first.

    A household can have two players going at once - a phone casting to the TV
    makes two sessions, and two rooms make two more - so this returns all of
    them instead of a single guess. "Best" is a session that is playing rather
    than paused, and the most recently active after that.
    """
    base, key = config(con)
    if not key:
        return []
    try:
        rows = _call(base, key, "/Sessions", timeout=15) or []
    except JellyfinError:
        return []
    found = []
    for row in rows:
        item = row.get("NowPlayingItem") or {}
        if not item.get("Id"):
            continue
        state = row.get("PlayState") or {}
        artists = item.get("Artists") or []
        found.append({
            "session": row.get("Id") or "",
            # short and specific: the box, and whose Jellyfin user it is
            "device": " \u00b7 ".join(
                part for part in ((row.get("DeviceName")
                                   or row.get("Client") or "Jellyfin app"),
                                  row.get("UserName")) if part),
            "client": row.get("Client") or "",
            "user": row.get("UserName") or "",
            "name": item.get("Name") or "",
            "kind": (item.get("Type") or "").lower(),
            "album": item.get("Album") or "",
            "artist": item.get("AlbumArtist") or (artists[0] if artists else ""),
            "series": item.get("SeriesName") or "",
            "episode": item.get("IndexNumber"),
            "season": item.get("ParentIndexNumber"),
            "item_id": item.get("Id") or "",
            "album_id": item.get("AlbumId") or "",
            "parent_id": item.get("ParentId") or "",
            "series_id": item.get("SeriesId") or "",
            "paused": bool(state.get("IsPaused")),
            "position": int((state.get("PositionTicks") or 0) // 10000000),
            "duration": int((item.get("RunTimeTicks") or 0) // 10000000),
            "at": row.get("LastActivityDate") or "",
        })
    found.sort(key=lambda item: (0 if item["paused"] else 1, item["at"]),
               reverse=True)
    return found


def now_playing(con=None, session=None):
    """What the pinned player should show: the session asked for, else the best.

    A session that has gone (someone pressed stop on the TV) falls back to the
    best remaining one, and the bar picks up that id from the reply - so a
    switched-to player that disappears does not leave the bar dead.
    """
    found = active_sessions(con)
    if session:
        for item in found:
            if item["session"] == session:
                return item
    return found[0] if found else None


CONTROLS = {"pause": "/Playing/Pause",
            "resume": "/Playing/Unpause",
            "stop": "/Playing/Stop",
            "next": "/Playing/NextTrack",
            "previous": "/Playing/PreviousTrack",
            "forward": "/Playing/FastForward",
            "rewind": "/Playing/Rewind"}


def control(con, session_id, action):
    """Pause, resume or stop what a client is playing. Returns a message."""
    base, key = config(con)
    path = CONTROLS.get(action)
    if not path:
        raise JellyfinError("Unknown playback control: %s" % (action,))
    if not session_id:
        raise JellyfinError("Nothing is playing on Jellyfin.")
    _call(base, key,
          "/Sessions/%s%s" % (urllib.parse.quote(session_id), path),
          method="POST", timeout=15)
    return "Jellyfin: %s sent" % action



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
    found = [dict(row) for row in con.execute(
        "SELECT id, label, url, api_key, is_active FROM jellyfin_servers"
        " ORDER BY id").fetchall()]
    routes = con.execute("SELECT COUNT(*) FROM digital_sources"
                         " WHERE provider = 'jellyfin'").fetchone()[0]
    return {"url": base,
            "configured": bool(key) or any(
                row["is_active"] for row in found),
            "servers": found,
            "routes": routes,
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
                      "started": time.strftime("%Y-%m-%d %H:%M")})
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
                      "finished": time.strftime("%Y-%m-%d %H:%M")})



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


def _size_label(size):
    """A byte count a person can weigh: "24.2 GB", "350 MB"."""
    if size >= 1000000000:
        return "%.1f GB" % (size / 1000000000.0)
    if size >= 1000000:
        return "%d MB" % round(size / 1000000.0)
    return ""


def _resolution_of(width, height):
    """The name a file's picture goes by: 4K, 1080p, 720p, else its height.

    Two hints disagree on letterboxed and 4:3 transfers - 1920x800 is a 1080p
    film, 1440x1080 is too - so each side is put in a tier and the better one
    wins. Only when neither reaches 720p does the raw height get printed.
    """
    tiers = ("", "720p", "1080p", "4K")
    rank = {name: place for place, name in enumerate(tiers)}
    by_width = ("4K" if width >= 3400 else "1080p" if width >= 1800
                else "720p" if width >= 1200 else "")
    by_height = ("4K" if height >= 1600 else "1080p" if height >= 900
                 else "720p" if height >= 650 else "")
    best = max((by_width, by_height), key=lambda name: rank[name])
    return best or ("%dp" % height if height else "")


def _sound_of(stream):
    """The audio track, named the way a person would: "DTS-HD MA 5.1"."""
    profile = (stream.get("Profile") or "").strip()
    codec = (stream.get("Codec") or "").upper()
    layout = (stream.get("ChannelLayout") or "").strip()
    if layout.lower() in ("stereo", "mono"):
        layout = "2.0" if layout.lower() == "stereo" else "1.0"
    name = profile if len(profile) > 3 else codec
    low = name.lower()
    if "atmos" in low:                      # "Dolby TrueHD with Dolby Atmos"
        name = "TrueHD Atmos" if "truehd" in low else "Atmos"
    elif "truehd" in low:
        name = "TrueHD"
    elif low.startswith(("dolby digital plus", "eac3", "e-ac-3")):
        name = "DD+"
    elif low.startswith(("dolby digital", "ac3", "ac-3")):
        name = "DD"
    if not name:
        name = (stream.get("DisplayTitle") or "").split(" - ")[0].strip()
    return " ".join(one for one in (name, layout) if one)


def _quality_of(source, size=None):
    """What one file is, in a line worth weighing up against the disc.

    Jellyfin describes the streams, so the picture (2160p Dolby Vision), the
    sound (TrueHD Atmos 7.1) and the size (24.2 GB) can be said properly. An
    item that carries no streams falls back to its MediaSource name, which is
    usually the same thing spelled out ("... - 2160p Dolby Vision, HDR10").
    """
    streams = source.get("MediaStreams") or []
    video = next((one for one in streams
                  if (one.get("Type") or "").lower() == "video"), {})
    audio = next((one for one in streams
                  if (one.get("Type") or "").lower() == "audio"), {})
    parts = []
    height = video.get("Height") or 0
    picture = _resolution_of(video.get("Width") or 0, height)
    if picture:
        parts.append(picture)
    kind = (video.get("VideoRangeType") or video.get("VideoRange") or "").upper()
    if kind.startswith("DOVI"):
        parts.append("Dolby Vision + HDR10" if "HDR10" in kind else "Dolby Vision")
    elif kind.startswith("HDR10PLUS"):
        parts.append("HDR10+")
    elif kind.startswith("HDR10"):
        parts.append("HDR10")
    elif kind == "HLG":
        parts.append("HLG")
    sound = _sound_of(audio) if audio else ""
    if sound:
        parts.append(sound)
    rate = source.get("BitRate") or audio.get("BitRate") or 0
    if not height and rate >= 100000:       # no picture: the rate is the story
        parts.append("%d kbps" % round(rate / 1000.0))
    label = _size_label(source.get("Size") or 0 if size is None else size)
    if label:
        parts.append(label)
    if not parts:                       # no streams: the name is all we have
        return _version_of(source.get("Name") or "")[1].strip()
    return " \u00b7 ".join(parts)


def _track_sources(base, key, jf_id):
    """The MediaSources of an album's tracks.

    A Jellyfin MusicAlbum has no files of its own: the Audio items under it do.
    One request per album, and only while its routes still have no quality.
    """
    import json
    import urllib.request
    url = ("%s/Items?ParentId=%s&IncludeItemTypes=Audio&Fields=MediaSources"
           "&SortBy=ParentIndexNumber,IndexNumber&Limit=200&apiKey=%s"
           % (base, jf_id, key))
    with urllib.request.urlopen(url, timeout=30) as response:
        data = json.load(response)
    found = []
    for track in data.get("Items") or []:
        found.extend(one for one in (track.get("MediaSources") or [])
                     if one.get("Id"))
    return found


def _album_quality(base, key, jf_id):
    """What an album's files are, from its first track and the total size."""
    tracks = _track_sources(base, key, jf_id)
    if not tracks:
        return ""
    total = sum((one.get("Size") or 0) for one in tracks)
    line = _quality_of(tracks[0], size=total)
    if len(tracks) <= 2:
        # A file-per-album rip, or a library that indexed one track of a
        # folder it holds: without the count the small size looks like a bug.
        line += " \u00b7 %d track%s" % (len(tracks),
                                     "" if len(tracks) == 1 else "s")
    return line


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
        if not sources:                 # an album: its tracks hold the files
            try:
                quality = _album_quality(base, key, jf_id)
            except Exception as exc:
                quality = ""
                log("jellyfin: tracks for %s skipped (%s)"
                    % (jf_id[:6], exc))
            if quality:
                con.execute(
                    "UPDATE digital_sources SET media_quality = ?"
                    " WHERE provider = 'jellyfin' AND external_id = ?"
                    "   AND (media_quality IS NULL OR media_quality <> ?)",
                    (quality, jf_id, quality))
            continue
        # What each file is, stamped on the route that reaches it. This runs
        # *before* the "single file" early return below: that is most of the
        # shelf, and the Play sheet needs the answer for exactly those. The
        # update is skipped when the text has not changed, so a re-encode or a
        # better spelling reaches the page on the next sync without rewriting
        # a thousand rows every time.
        for source in sources:
            quality = _quality_of(source)
            if quality:
                con.execute(
                    "UPDATE digital_sources SET media_quality = ?"
                    " WHERE provider = 'jellyfin' AND external_id = ?"
                    "   AND (media_source_id = ? OR media_source_id IS NULL)"
                    "   AND (media_quality IS NULL OR media_quality <> ?)",
                    (quality, jf_id, source.get("Id"), quality))
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



def _artist_albums(base, key, term, limit=40):
    """Albums by every artist whose name matches a term.

    Jellyfin's `searchTerm` looks at item *names*, so "ed sheeran" finds no
    albums at all - the artist is a separate field - and an album called "+"
    is unreachable by typing either the act or the title. Asking the artists
    endpoint first and then listing their albums is what makes such a title
    linkable by hand.
    """
    artists = _call(base, key, "/Artists", method="GET",
                    params={"searchTerm": term, "limit": "3",
                            "Fields": "ItemCount"}) or {}
    ids = [item.get("Id") for item in artists.get("Items") or []
           if item.get("Id")]
    found = []
    for artist_id in ids[:2]:
        albums = _call(base, key, "/Items", method="GET",
                       params={"ArtistIds": artist_id, "recursive": "true",
                               "limit": str(limit),
                               "IncludeItemTypes": "MusicAlbum",
                               "Fields": "Path,ProductionYear,AlbumArtist"})
        found.extend((albums or {}).get("Items") or [])
    return found


def search_items(con, term, limit=20):
    """Jellyfin items matching a term, for picking a match by hand."""
    base, key = config(con)
    found = _call(base, key, "/Items", method="GET", params={
        "searchTerm": term, "recursive": "true", "limit": str(limit),
        "IncludeItemTypes": "Movie,MusicAlbum,Series,Episode",
        "Fields": "Path,ProductionYear"})
    items = list((found or {}).get("Items") or [])
    seen = {item.get("Id") for item in items}
    for item in _artist_albums(base, key, term, limit):
        if item.get("Id") not in seen:
            seen.add(item.get("Id"))
            items.append(item)
    rows = []
    for item in items[:limit]:
        rows.append({
            "id": item.get("Id") or "",
            "name": item.get("Name") or "",
            "kind": item.get("Type") or "",
            "year": item.get("ProductionYear"),
            "path": item.get("Path") or "",
        })
    return rows


def describe(con, jf_id):
    """(name, kind, path) for one Jellyfin id."""
    base, key = config(con)
    found = _call(base, key, "/Items", method="GET", params={
        "ids": jf_id, "limit": "1", "Fields": "Path"})
    items = (found or {}).get("Items") or []
    if not items:
        return "", "", ""
    item = items[0]
    return (item.get("Name") or "", (item.get("Type") or "").lower(),
            item.get("Path") or "")



def link_item(con, title_id, jf_id, server=None):
    """Give one Jellyfin item a copy of its own, picked by hand.

    A Jellyfin route hangs off a *copy*, and a Jellyfin copy is a copy of its
    own (format 'ripped', owned by the 'rips' user), exactly as materialise()
    makes them. Returns the new copy id, or None when that item is already
    linked somewhere.
    """
    server = server or (servers(con) or [None])[0]
    if server is None:
        raise JellyfinError("No Jellyfin server configured")
    owner = con.execute("SELECT id FROM users WHERE username = 'rips'"
                        ).fetchone()
    if owner is None:
        raise JellyfinError("No 'rips' user to own the copy")
    already = con.execute(
        "SELECT 1 FROM digital_sources WHERE provider = 'jellyfin'"
        "   AND server_id = ? AND external_id = ?",
        (server["id"], jf_id)).fetchone()
    if already:
        return None
    name, kind, path = describe(con, jf_id)
    # jellyfin_links.jf_kind only speaks these words. describe() hands
    # back Jellyfin's own Type ("musicalbum"), which fails the CHECK,
    # so a hand-picked album link could never be saved.
    row = con.execute("SELECT kind FROM titles WHERE id = ?",
                      (title_id,)).fetchone()
    music = bool(row and row[0] == "music")
    kind = {"musicalbum": "album", "audio": "album",
            "musicvideo": "album", "movie": "movie",
            "boxset": "movie", "series": "series",
            "season": "season", "episode": "episode"}.get(
                (kind or "").lower(), "album" if music else "movie")
    copy_id = con.execute(
        "INSERT INTO items (title_id, owner_id, format, visibility, added_by)"
        " VALUES (?, ?, 'ripped', 'shared', ?)",
        (title_id, owner["id"], owner["id"])).lastrowid
    con.execute(
        "INSERT INTO digital_sources (item_id, provider, server_id,"
        " external_id, notes) VALUES (?, 'jellyfin', ?, ?, ?)",
        (copy_id, server["id"], jf_id, server["label"]))
    con.execute(
        "INSERT OR REPLACE INTO jellyfin_links (item_id, jf_id, jf_kind,"
        " jf_name, jf_path, matched_by, server_id, synced_at)"
        # matched_by is CHECKed to ('path', 'name'); a hand-picked link is
        # recorded as 'name', the closest of the two
        " VALUES (?, ?, ?, ?, ?, 'name', ?, datetime('now'))",
        (copy_id, jf_id, kind, name, path, server["id"]))
    con.commit()
    return copy_id
