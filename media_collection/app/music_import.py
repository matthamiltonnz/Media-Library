#!/usr/bin/env python3
"""Import albums from a ripped music tree into the catalogue.

Layout expected (one level per owner, then artist, then album):

    <root>/Matt's Music/ABBA/Gold (1992)/01 - Dancing Queen.flac

Each album folder becomes a music title with one CD copy owned by that person and a
playback source pointing at the folder. Idempotent - re-running adds nothing.

usage: music_import.py DB /media/QNAP/Music [--root-label Music] [--dry-run]
"""
import argparse
import os
import re
import sqlite3
import sys

YEAR = re.compile(r"\((\d{4})\)")


def is_junk(name):
    """NAS housekeeping folders (.@__thumb, @eaDir, @Recycle, hidden entries)."""
    return (not name or name.startswith(".") or name.startswith("@")
            or name.lower() in ("recycle", "#recycle"))


AUDIO_EXT = (".mp3", ".flac", ".m4a", ".wav", ".ogg", ".ape", ".wma", ".aac",
             ".aiff", ".aif", ".dsf", ".dff", ".alac", ".opus", ".mpc", ".wv")


DISC_EXT = (".mka", ".mkv", ".cue")


def album_format(folder, depth=2):
    """(format, edition) for an album folder.

    FLAC means a ripped CD, MP3 means a Google Play Music purchase and M4A/AAC means
    an iTunes purchase, so the copy is recorded as Digital rather than CD. Artwork-only
    leftovers never become a copy; a concert disc (mka/mkv/cue without separate tracks,
    e.g. an Atmos Blu-ray) is kept as a Blu-ray copy.
    """
    base = len(folder)
    flac = mp3 = aac = disc = False
    for root, dirs, files in os.walk(folder):
        dirs[:] = [name for name in dirs if not is_junk(name)]
        for name in files:
            lower = name.lower()
            if lower.endswith(".flac"):
                flac = True
            elif lower.endswith(".mp3"):
                mp3 = True
            elif lower.endswith((".m4a", ".aac", ".m4p")):
                aac = True
            elif lower.endswith(DISC_EXT):
                disc = True
        if flac or mp3 or aac:
            break
        if root[base:].count(os.sep) >= depth:
            dirs[:] = []
    if flac:
        return "cd", ""
    if mp3:
        return "digital", "Google Play \u00b7 MP3"
    if aac:
        return "digital", "iTunes \u00b7 AAC"
    return ("bluray", "") if disc else (None, "")


def parse_owner(folder):
    name = folder.replace("\u2019", "'").replace("\u2018", "'")
    match = re.match(r"(.+?)'s music$", name, re.I)
    return match.group(1).strip() if match else None


def parse_album(folder):
    match = YEAR.search(folder)
    if not match:
        return folder, None
    clean = (folder[:match.start()] + folder[match.end():]).strip(" -_.")
    return (clean or folder), int(match.group(1))


def resolve_root(con, given, dry_run, label):
    """(root_id, root_path, music_dir) - the media root and the Music folder inside it.

    The media root is the Home Assistant media folder (/media/QNAP); 'Music' plus the
    owner/artist/album folders form the playback-source path under it.
    """
    rows = con.execute("SELECT id, path FROM media_roots"
                       " ORDER BY length(path) DESC").fetchall()
    if given:
        music_dir = given.rstrip("/")
    else:
        music_dir = None
        for row in rows:
            candidate = os.path.join(row["path"].rstrip("/"), "Music")
            if os.path.isdir(candidate):
                music_dir = candidate
                break
        if music_dir is None:
            row = con.execute("SELECT id, path FROM media_roots"
                              " WHERE medium = 'music' ORDER BY id LIMIT 1").fetchone()
            if row is None:
                return None, None, None
            return row["id"], row["path"].rstrip("/"), row["path"].rstrip("/")

    for row in rows:
        root = row["path"].rstrip("/")
        if music_dir == root or music_dir.startswith(root + "/"):
            return row["id"], root, music_dir

    root_path = os.path.dirname(music_dir)
    if dry_run:
        return None, root_path, music_dir
    if con.execute("SELECT 1 FROM media_roots WHERE label = ?",
                   (label,)).fetchone():
        label = "%s (%s)" % (label, os.path.basename(root_path) or root_path)
    root_id = con.execute(
        "INSERT INTO media_roots (label, path, medium) VALUES (?, ?, 'video')",
        (label, root_path)).lastrowid
    return root_id, root_path, music_dir


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("db")
    parser.add_argument("root", nargs="?",
                        help="music root as the add-on sees it; defaults to the "
                             "first media_roots row with medium='music'")
    parser.add_argument("--root-label", default="Music")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    con = sqlite3.connect(args.db, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")

    root_id, root_path, music_dir = resolve_root(con, args.root, args.dry_run,
                                                 args.root_label)
    if music_dir is None:
        print("music: no music folder under the media roots, nothing to do")
        return 0
    if not os.path.isdir(music_dir):
        print("music: %s is not mounted, nothing to do" % music_dir)
        return 0
    prefix = os.path.relpath(music_dir, root_path).replace(os.sep, "/")

    albums = copies = owners = 0
    for owner_dir in sorted(os.listdir(music_dir)):
        if is_junk(owner_dir):
            continue
        owner = parse_owner(owner_dir)
        owner_path = os.path.join(music_dir, owner_dir)
        if not owner or not os.path.isdir(owner_path):
            continue

        user = con.execute("SELECT id FROM users WHERE lower(display_name) = lower(?)",
                           (owner,)).fetchone()
        if user:
            owner_id = user["id"]
        elif args.dry_run:
            owner_id = -1
        else:
            owner_id = con.execute(
                "INSERT INTO users (username, display_name) VALUES (?, ?)",
                ("music-" + re.sub(r"[^a-z0-9]+", "-", owner.lower()).strip("-"), owner)
            ).lastrowid
            owners += 1

        for artist in sorted(os.listdir(owner_path)):
            if is_junk(artist):
                continue
            artist_path = os.path.join(owner_path, artist)
            if not os.path.isdir(artist_path):
                continue
            for album in sorted(os.listdir(artist_path)):
                if is_junk(album):
                    continue
                album_path = os.path.join(artist_path, album)
                if not os.path.isdir(album_path):
                    continue
                fmt, edition = album_format(album_path)
                if not fmt:
                    continue

                rel_path = "/".join(part for part in (prefix, owner_dir, artist, album)
                                    if part and part != ".")
                already = con.execute(
                    "SELECT 1 FROM digital_sources WHERE root_id = ? AND path = ?",
                    (root_id, rel_path)).fetchone() if root_id else None
                if already:
                    continue

                clean_album, year = parse_album(album)
                title_name = "%s — %s" % (artist, clean_album)
                albums += 1
                copies += 1
                if args.dry_run:
                    continue

                title = con.execute(
                    "SELECT id FROM titles WHERE kind = 'music' AND lower(name) = lower(?)",
                    (title_name,)).fetchone()
                title_id = title["id"] if title else con.execute(
                    "INSERT INTO titles (name, release_year, kind) VALUES (?, ?, 'music')",
                    (title_name, year)).lastrowid

                item_id = con.execute(
                    "INSERT INTO items (title_id, owner_id, format, edition,"
                    " location, visibility) VALUES (?, ?, ?, ?, ?, 'shared')",
                    (title_id, owner_id, fmt, edition or None, None)).lastrowid
                con.execute(
                    "INSERT INTO digital_sources (item_id, provider, path, root_id)"
                    " VALUES (?, 'rip', ?, ?)", (item_id, rel_path, root_id))

    if not args.dry_run:
        con.commit()
    con.close()
    print("music: %d albums, %d copies%s, %d new owner(s)"
          % (albums, copies, " (dry run)" if args.dry_run else "", owners))
    return 0


if __name__ == "__main__":
    sys.exit(main())
