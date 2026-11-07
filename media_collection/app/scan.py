#!/usr/bin/env python3
"""Match copies to the files already sitting in the media folders.

The catalogue records what the household owns; the media folders hold what has
actually been ripped. Rather than walking a whole share, this looks only where
each title should be - Movies/Name (Year), TV/Show (Year), Music/owner/artist/
album - so a scan costs a handful of folder listings even on a NAS.
"""

import json
import os
import re
import sqlite3
import threading
import time
import unicodedata

SKIP_FOLDERS = {"@recycle", "#recycle", "@eadir", "system volume information",
                "lost+found"}

MEDIA_EXTENSIONS = (
    ".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".mpg", ".mpeg", ".ts",
    ".m2ts", ".iso", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav",
    ".wma", ".ape", ".aiff",
)

YEAR_IN_NAME = re.compile(r"\(\s*(\d{4})\s*\)")
ARTIST_ALBUM = re.compile(r"\s+[—–-]\s+|\s+-\s+")


def _fold(text):
    """Loose key: case, accents, punctuation and a leading article do not matter.

    'Four Seasons, The' and 'The Four Seasons' fold to the same key, which is how
    sort-name style titles are written in the catalogue but not on the disk.
    """
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = text.lower()
    text = re.sub(r",\s*(the|a|an)\s*$", "", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"^(the|a|an) ", "", text)


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


counted = set()


def _listdir(path, cache):
    """One listing per folder per run, so a slow share is read once."""
    if path not in cache:
        try:
            cache[path] = sorted(name for name in os.listdir(path)
                                 if name.lower() not in SKIP_FOLDERS)
        except OSError:
            cache[path] = []
    if path not in counted:
        counted.add(path)
        STATE["files"] += sum(1 for name in cache.get(path) or ()
                              if "." in name)
    return cache[path]


def _has_media(path, cache):
    """True when the folder actually holds a film, episode or track.

    Empty stubs left behind by an earlier import look like albums by name, so the
    scan only trusts a folder that has something in it to play.
    """
    return any(name.lower().endswith(MEDIA_EXTENSIONS)
               for name in _listdir(path, cache))


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
    found = []
    for root, relative in _candidates(roots, cache):
        stem, entry_year = _split_year(relative.rsplit("/", 1)[-1])
        if not (_keys(stem) & wanted):
            continue
        if kind == "tv" and "/" in relative:
            continue
        if not _has_media(os.path.join(root["path"], relative), cache):
            continue
        if year and entry_year == year:
            return relative, root["id"]
        found.append((entry_year, relative, root["id"]))
    if year:
        # no folder says the year - only trust one that says nothing either
        quiet = [option for option in found if not option[0]]
        return (quiet[0][1], quiet[0][2]) if len(quiet) == 1 else None
    # a title with no year is only safe when one folder carries that name
    return (found[0][1], found[0][2]) if len(found) == 1 else None


def _album_match(entry, album_key, artist_key):
    """Does this folder stand for the album?

    Albums get renamed in the wild - 'The Four Seasons Hits' holding 'Hits
    Digitally Enhanced' - so accept the album name itself, or the artist followed
    by the start of it.
    """
    key = _fold(_split_year(entry)[0])
    if not key:
        return False
    if key == album_key:
        return True
    joined = artist_key + " " + album_key
    flat, flat_joined = key.replace(" ", ""), joined.replace(" ", "")
    if flat == flat_joined or (flat_joined.startswith(flat) and len(flat) >= 8):
        return True
    return len(album_key) >= 8 and key.startswith(album_key)


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
                    if not _album_match(entry, album_key, artist_key):
                        continue
                    if not _has_media(os.path.join(artist_path, entry), cache):
                        continue
                    return "Music/%s/%s/%s" % (owner, name, entry), root["id"]
    return None


SKIP_KEY = "scan_skipped"


def _skipped(con):
    """Files the household said are the wrong ones, as 'title id|folder'."""
    try:
        row = con.execute("SELECT value FROM settings WHERE key = ?",
                          (SKIP_KEY,)).fetchone()
        return set(json.loads(row["value"] or "[]")) if row else set()
    except (TypeError, ValueError):
        return set()


def remember_skip(con, title_id, path):
    """Stop a later scan putting back a source someone deliberately removed."""
    if not path:
        return
    skipped = _skipped(con)
    skipped.add("%d|%s" % (title_id, path))
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (SKIP_KEY, json.dumps(sorted(skipped))))
    con.commit()


def rip_item(con, title_id, path=None):
    """The copy that stands for the file on the NAS, made the first time it is seen.

    A ripped file is its own copy, so a library of nothing but files still shows
    something on the shelf and the disc copies stay untouched.
    """
    if path:
        # a file belongs to the copy that already holds that exact path;
        # failing that, to a copy with no file at all; never to a copy
        # that some other file is already using
        found = con.execute(
            "SELECT i.id FROM items i JOIN digital_sources d ON d.item_id = i.id"
            " WHERE i.title_id = ? AND d.provider = 'rip' AND d.path = ?"
            " LIMIT 1", (title_id, path)).fetchone()
        if not found:
            found = con.execute(
                "SELECT i.id FROM items i"
                " WHERE i.title_id = ? AND i.format = 'ripped'"
                "   AND NOT EXISTS (SELECT 1 FROM digital_sources d"
                "                    WHERE d.item_id = i.id AND d.provider = 'rip')"
                " ORDER BY i.id LIMIT 1", (title_id,)).fetchone()
    else:
        found = con.execute(
            "SELECT i.id FROM items i JOIN digital_sources d ON d.item_id = i.id"
            " WHERE i.title_id = ? AND d.provider = 'rip'"
            " AND i.format = 'ripped' LIMIT 1",
        (title_id,)).fetchone()
    if found:
        return found["id"]
    owner = con.execute(
        "SELECT owner_id FROM items WHERE title_id = ? ORDER BY id LIMIT 1",
        (title_id,)).fetchone()
    if owner:
        owner_id = owner["owner_id"]
    else:
        user = con.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
        if not user:
            return None
        owner_id = user["id"]
    return con.execute(
        "INSERT INTO items (title_id, owner_id, format, visibility, notes)"
        " VALUES (?, ?, 'ripped', 'shared', 'ripped file')",
        (title_id, owner_id)).lastrowid


def missing(con):
    """Titles that have a copy but no ripped file linked to any of them."""
    return con.execute(
        "SELECT t.id, t.name, t.kind, t.release_year FROM titles t"
        " JOIN items i ON i.title_id = t.id"
        " WHERE NOT EXISTS (SELECT 1 FROM digital_sources d"
        "   JOIN items owned ON owned.id = d.item_id"
        "   WHERE owned.title_id = t.id AND d.provider = 'rip')"
        " GROUP BY t.id ORDER BY t.id").fetchall()


def sync_files(con, log=print, repair=False):
    """Link every title that is already on disk but has no playback source."""
    roots = _roots(con)
    cache, counts = {}, {"roots": len(roots), "titles": 0, "linked": 0,
                         "unmatched": 0, "missing_roots": 0, "skipped": 0,
                         "relinked": 0}
    counts["missing_roots"] = sum(1 for root in roots
                                  if not os.path.isdir(root["path"]))
    if not roots:
        return counts
    skipped = _skipped(con)
    for title in missing(con):
        counts["titles"] += 1
        found = _locate(roots, title, cache)
        if not found:
            counts["unmatched"] += 1
            continue
        relative, root_id = found
        if "%d|%s" % (title["id"], relative) in skipped:
            counts["skipped"] = counts.get("skipped", 0) + 1
            continue
        item_id = rip_item(con, title["id"], relative)
        if not item_id:
            counts["unmatched"] += 1
            continue
        con.execute("INSERT INTO digital_sources (item_id, provider, path, root_id, notes)"
                    " VALUES (?, 'rip', ?, ?, ?)",
                    (item_id, relative, root_id, "Found in the media folder"))
        counts["linked"] += 1
        log("scan: %s -> %s" % (title["name"], relative))
    con.commit()
    counts["repointed"] = repoint_rips(con, log)
    if repair:
        counts["relinked"] = _relink(con, roots, cache, skipped, log)
        con.commit()
    counts["folders"] = len(cache)
    return counts


def repoint_rips(con, log):
    """Move rip sources off disc copies onto the title's ripped-file copy.

    Older scans hung the file on whichever copy came first, so a ripped film looked
    like a Blu-ray with no file. Moving them keeps one copy per thing owned.
    """
    rows = con.execute(
        "SELECT d.id, d.item_id, i.title_id, i.format, d.path"
        " FROM digital_sources d"
        " JOIN items i ON i.id = d.item_id"
        " WHERE d.provider = 'rip' AND i.format != 'digital'").fetchall()
    moved = 0
    for row in rows:
        target = rip_item(con, row["title_id"], row["path"])
        if not target or target == row["item_id"]:
            continue
        con.execute("UPDATE digital_sources SET item_id = ? WHERE id = ?",
                    (target, row["id"]))
        moved += 1
    if moved:
        con.commit()
        log("scan: %d ripped file(s) moved to their own copy" % moved)
    return moved


def _relink(con, roots, cache, skipped, log):
    """Point sources at the folder that actually holds the files.

    Albums and films get renamed on the share while the catalogue keeps the old
    folder, which then has no media in it - so look again and move the source.
    """
    by_root = {root["id"]: root for root in roots}
    relinked = 0
    rows = con.execute(
        "SELECT d.id, d.path, d.root_id, t.id AS title_id, t.name, t.kind,"
        " t.release_year FROM digital_sources d JOIN items i ON i.id = d.item_id"
        " JOIN titles t ON t.id = i.title_id"
        " WHERE d.provider = 'rip' AND d.path IS NOT NULL AND d.path <> ''").fetchall()
    for row in rows:
        root = by_root.get(row["root_id"])
        if root is None or not os.path.isdir(root["path"]):
            continue
        if _has_media(os.path.join(root["path"], row["path"]), cache):
            continue
        found = _locate(roots, row, cache)
        if not found or found[0] == row["path"]:
            continue
        if "%d|%s" % (row["title_id"], found[0]) in skipped:
            continue
        con.execute("UPDATE digital_sources SET path = ?, root_id = ?,"
                    " notes = 'Re-linked to the folder found on disk' WHERE id = ?",
                    (found[0], found[1], row["id"]))
        relinked += 1
        log("scan: %s moved to %s" % (row["name"], found[0]))
    return relinked


def suggest(con, title_id):
    """Where this title's file already sits, for pre-filling the add-copy form."""
    row = con.execute("SELECT id, name, kind, release_year FROM titles WHERE id = ?",
                      (title_id,)).fetchone()
    if not row:
        return ""
    found = _locate(_roots(con), row, {})
    return found[0] if found else ""


_LOCK = threading.Lock()
STATE = {"running": False, "started": None, "finished": None,
         "counts": {}, "files": 0, "folders": 0}


def status():
    return dict(STATE)


def summary():
    """One line for the admin page: what the last scan found."""
    state = status()
    if state["running"]:
        return ("Scanning the media folders - %s files in %s folders"
                " so far." % (format(state.get("files", 0), ","),
                               format(state.get("folders", 0), ",")))
    counts = state.get("counts") or {}
    if not counts:
        return ""
    if counts.get("error"):
        return "Media scan failed: %s" % counts["error"]
    text = ("Media scan: %(linked)d copies linked, %(unmatched)d titles with no "
            "file found (of %(titles)d)." % counts)
    if counts.get("relinked"):
        text += " %(relinked)d moved to a renamed folder." % counts
    return text


def start(db_path, repair=False):
    """Look for unlinked files in the background; a page must never wait for it."""
    with _LOCK:
        if STATE["running"]:
            return False
        counted.clear()
        STATE.update({"running": True, "counts": {},
                      "files": 0, "repair": repair,
                      "started": time.strftime("%Y-%m-%d %H:%M"),
                      "finished": None})
    threading.Thread(target=_run, args=(db_path, repair), daemon=True).start()
    return True


def _run(db_path, repair=False):
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        counts = sync_files(con, log=lambda message: None, repair=repair)
        if counts["linked"]:
            print("scan: linked %d copies from the media folders" % counts["linked"])
        if counts.get("relinked"):
            print("scan: moved %d copies to the folder found on disk" % counts["relinked"])
    except Exception as exc:                          # never take the server down
        print("scan: failed: %s" % exc)
        counts = {"error": str(exc)}
    finally:
        con.close()
    with _LOCK:
        STATE.update({"running": False, "counts": counts,
                      "finished": time.strftime("%Y-%m-%d %H:%M")})
