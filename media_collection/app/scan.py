#!/usr/bin/env python3
"""Match copies to the files already sitting in the media folders.

The catalogue records what the household owns; the media folders hold what has
actually been ripped. Rather than walking a whole share, this looks only where
each title should be - Movies/Name (Year), TV/Show (Year), Music/owner/artist/
album - so a scan costs a handful of folder listings even on a NAS.
"""

import os
import re
import sqlite3
import threading
import time
import unicodedata

SKIP_FOLDERS = {"@recycle", "#recycle", "@eadir", "system volume information",
                "lost+found"}

YEAR_IN_NAME = re.compile(r"\(\s*(\d{4})\s*\)")
ARTIST_ALBUM = re.compile(r"\s+[—–-]\s+|\s+-\s+")


def _fold(text):
    """Loose key: case, accents, punctuation and a leading 'the ' do not matter."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    text = re.sub(r"\s+", " ", text).strip()
    return text[4:] if text.startswith("the ") else text


def _split_year(name):
    """'127 Hours (2010)' -> ('127 Hours', 2010)."""
    match = YEAR_IN_NAME.search(name or "")
    if not match:
        return (name or "").strip(), 0
    return (name[:match.start()] + name[match.end():]).strip(), int(match.group(1))


def _year(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _keys(text):
    """Every loose spelling we accept as the same title.

    '10,000 B.C.' and '10 000 bc', 'Tintin: Secret of the Unicorn' and 'Tintin'
    all fold to a shared key, so punctuation and subtitles do not hide a file.
    """
    keys = set()
    for part in (text or "", (text or "").split(":")[0]):
        folded = _fold(part)
        if folded:
            keys.add(folded)
            keys.add(folded.replace(" ", ""))
    return keys


def _listdir(path, cache):
    """One listing per folder per run, so a slow share is read once."""
    if path not in cache:
        try:
            cache[path] = sorted(name for name in os.listdir(path)
                                 if name.lower() not in SKIP_FOLDERS)
        except OSError:
            cache[path] = []
    return cache[path]


def _roots(con):
    return con.execute("SELECT id, path FROM media_roots WHERE path IS NOT NULL"
                       " AND path <> '' ORDER BY id").fetchall()


def _folder_in(root, relative, cache):
    """The entry under 'relative' whose name matches, as (name, year)."""
    base = os.path.join(root["path"], relative)
    for entry in _listdir(base, cache):
        if not os.path.isdir(os.path.join(base, entry)):
            continue
        yield entry, _split_year(entry)


def _candidates(roots, cache):
    """Every folder up to two levels below each root, as (root, relative).

    Films sit in 'Movies', 'iTunes Movies' and the odd personal folder, so the
    scan looks one folder deep everywhere instead of trusting one fixed name.
    """
    for root in roots:
        for top in _listdir(root["path"], cache):
            top_path = os.path.join(root["path"], top)
            if not os.path.isdir(top_path):
                continue
            yield root, top
            for second in _listdir(top_path, cache):
                if os.path.isdir(os.path.join(top_path, second)):
                    yield root, top + "/" + second


def _locate(roots, title, cache):
    """(relative path, root id) where this title's file lives, or None."""
    kind = title["kind"] if "kind" in title.keys() else "movie"
    name = title["name"] or ""
    if kind == "music":
        parts = ARTIST_ALBUM.split(name, maxsplit=1)
        if len(parts) != 2:
            return None
        return _locate_album(roots, parts[0].strip(), parts[1].strip(), cache)

    year = _year(title["release_year"])
    wanted = _keys(name)
    for root, relative in _candidates(roots, cache):
        stem, entry_year = _split_year(relative.rsplit("/", 1)[-1])
        if not (_keys(stem) & wanted):
            continue
        if year and entry_year and entry_year != year:
            continue
        if kind == "tv" and "/" in relative:
            continue
        return relative, root["id"]
    return None


def _locate_album(roots, artist, album, cache):
    """'Music/<owner>/<artist>/<album>' as it appears on disk, or None."""
    artist_key, album_key = _fold(artist), _fold(_split_year(album)[0] or album)
    for root in roots:
        for owner, _ in _folder_in(root, "Music", cache):
            owner_path = os.path.join(root["path"], "Music", owner)
            for name in _listdir(owner_path, cache):
                if _fold(name) != artist_key:
                    continue
                artist_path = os.path.join(owner_path, name)
                if not os.path.isdir(artist_path):
                    continue
                for entry in _listdir(artist_path, cache):
                    if _fold(_split_year(entry)[0]) != album_key:
                        continue
                    return "Music/%s/%s/%s" % (owner, name, entry), root["id"]
    return None


def missing(con):
    """Titles that have a copy but no ripped file linked to any of them."""
    return con.execute(
        "SELECT t.id, t.name, t.kind, t.release_year FROM titles t"
        " JOIN items i ON i.title_id = t.id"
        " WHERE NOT EXISTS (SELECT 1 FROM digital_sources d"
        "   JOIN items owned ON owned.id = d.item_id"
        "   WHERE owned.title_id = t.id AND d.provider = 'rip')"
        " GROUP BY t.id ORDER BY t.id").fetchall()


def sync_files(con, log=print):
    """Link every title that is already on disk but has no playback source."""
    roots = _roots(con)
    cache, counts = {}, {"roots": len(roots), "titles": 0, "linked": 0,
                         "unmatched": 0, "missing_roots": 0}
    counts["missing_roots"] = sum(1 for root in roots
                                  if not os.path.isdir(root["path"]))
    if not roots:
        return counts
    for title in missing(con):
        counts["titles"] += 1
        found = _locate(roots, title, cache)
        if not found:
            counts["unmatched"] += 1
            continue
        relative, root_id = found
        item = con.execute("SELECT id FROM items WHERE title_id = ?"
                           " ORDER BY id LIMIT 1", (title["id"],)).fetchone()
        if not item:
            counts["unmatched"] += 1
            continue
        con.execute("INSERT INTO digital_sources (item_id, provider, path, root_id, notes)"
                    " VALUES (?, 'rip', ?, ?, ?)",
                    (item["id"], relative, root_id, "Found in the media folder"))
        counts["linked"] += 1
        log("scan: %s -> %s" % (title["name"], relative))
    con.commit()
    counts["folders"] = len(cache)
    return counts


def suggest(con, title_id):
    """Where this title's file already sits, for pre-filling the add-copy form."""
    row = con.execute("SELECT id, name, kind, release_year FROM titles WHERE id = ?",
                      (title_id,)).fetchone()
    if not row:
        return ""
    found = _locate(_roots(con), row, {})
    return found[0] if found else ""


_LOCK = threading.Lock()
STATE = {"running": False, "started": None, "finished": None, "counts": {}}


def status():
    return dict(STATE)


def summary():
    """One line for the admin page: what the last scan found."""
    state = status()
    if state["running"]:
        return "Scanning the media folders..."
    counts = state.get("counts") or {}
    if not counts:
        return ""
    if counts.get("error"):
        return "Media scan failed: %s" % counts["error"]
    return ("Media scan: %(linked)d copies linked, %(unmatched)d titles with no "
            "file found (of %(titles)d)." % counts)


def start(db_path):
    """Look for unlinked files in the background; a page must never wait for it."""
    with _LOCK:
        if STATE["running"]:
            return False
        STATE.update({"running": True, "counts": {},
                      "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "finished": None})
    threading.Thread(target=_run, args=(db_path,), daemon=True).start()
    return True


def _run(db_path):
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        counts = sync_files(con, log=lambda message: None)
        if counts["linked"]:
            print("scan: linked %d copies from the media folders" % counts["linked"])
    except Exception as exc:                          # never take the server down
        print("scan: failed: %s" % exc)
        counts = {"error": str(exc)}
    finally:
        con.close()
    with _LOCK:
        STATE.update({"running": False, "counts": counts,
                      "finished": time.strftime("%Y-%m-%d %H:%M:%S")})
