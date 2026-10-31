#!/usr/bin/env python3
"""Import TV shows from <root>/<Show (Year)>/<Season NN>/<episodes>.

One title per show (kind 'tv'), one Digital copy per season folder, one rip source
per season. Idempotent - a season that already has a source is skipped.

usage: tv_import.py DB [ROOT] [--dry-run]
"""
import argparse
import os
import re
import sqlite3
import sys

VIDEO_EXT = (".mkv", ".mp4", ".m4v", ".avi", ".ts", ".mov", ".wmv", ".mpg",
             ".mpeg", ".mts", ".flv", ".webm")
SEASON = re.compile(r"^(?:season|series|disc|dvd|vol(?:ume)?)\s*(\d+)$", re.I)
YEAR = re.compile(r"\((\d{4})\)\s*$")


def is_junk(name):
    """NAS housekeeping folders (.@__thumb, @eaDir, @Recycle, hidden entries)."""
    return (not name or name.startswith(".") or name.startswith("@")
            or name.lower() in ("recycle", "#recycle"))


def parse_show(folder):
    """'Firefly (2002)' -> ('Firefly', 2002)."""
    match = YEAR.search(folder)
    if match:
        name = folder[:match.start()].strip(" -_.")
        return name or folder, int(match.group(1))
    return folder.strip(), None


def episodes(folder, depth=2):
    """(count, extensions) of video files in the folder, disc folders included."""
    base = len(folder)
    count, exts = 0, set()
    for root, dirs, files in os.walk(folder):
        dirs[:] = [name for name in dirs if not is_junk(name)]
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext in VIDEO_EXT:
                count += 1
                exts.add(ext)
        if root[base:].count(os.sep) >= depth:
            dirs[:] = []
    return count, exts


def seasons(show_path):
    """[(label, path, episode count, extensions)] - season folders, else the show."""
    found = []
    for name in sorted(os.listdir(show_path)):
        path = os.path.join(show_path, name)
        if not os.path.isdir(path) or is_junk(name):
            continue
        count, exts = episodes(path)
        if not count:
            continue
        match = SEASON.match(name.strip())
        label = "Season %s" % match.group(1) if match else name
        found.append((label, path, count, exts))
    if found:
        return found
    count, exts = episodes(show_path, depth=0)
    return [("Episodes", show_path, count, exts)] if count else []


def default_root(con):
    """The media root that looks like a TV library."""
    rows = con.execute("SELECT id, label, path FROM media_roots WHERE medium = 'video'"
                       " ORDER BY label").fetchall()
    fallback = None
    for row in rows:
        path = row["path"]
        if not os.path.isdir(path):
            continue
        if "tv" in row["label"].lower():
            return row
        for name in os.listdir(path):
            show = os.path.join(path, name)
            if os.path.isdir(show) and any(
                    SEASON.match(child.strip())
                    for child in os.listdir(show)
                    if os.path.isdir(os.path.join(show, child))):
                fallback = fallback or row
    return fallback


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("db")
    parser.add_argument("root", nargs="?", help="TV root; defaults to the TV media root")
    parser.add_argument("--root-label", default="TV Shows")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    con = sqlite3.connect(args.db, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")

    if args.root:
        root_path = args.root.rstrip("/")
        row = con.execute("SELECT id FROM media_roots WHERE path = ?",
                          (root_path,)).fetchone()
        if row:
            root_id = row["id"]
        elif args.dry_run:
            root_id = None
        else:
            label = args.root_label
            if con.execute("SELECT 1 FROM media_roots WHERE label = ?",
                           (label,)).fetchone():
                label = "%s (%s)" % (label, os.path.basename(root_path) or root_path)
            root_id = con.execute(
                "INSERT INTO media_roots (label, path, medium)"
                " VALUES (?, ?, 'video')", (label, root_path)).lastrowid
    else:
        row = default_root(con)
        if row is None:
            print("tv: no TV root registered, nothing to do")
            return 0
        root_id, root_path = row["id"], row["path"].rstrip("/")

    if not os.path.isdir(root_path):
        print("tv: %s is not mounted, nothing to do" % root_path)
        return 0

    owner = con.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
    if owner is None:
        print("tv: no users yet, nothing to do")
        return 0
    owner_id = owner["id"]

    shows = copies = 0
    for name in sorted(os.listdir(root_path)):
        show_path = os.path.join(root_path, name)
        if not os.path.isdir(show_path) or is_junk(name):
            continue
        title_name, year = parse_show(name)
        found = seasons(show_path)
        if not found:
            continue
        shows += 1
        for label, path, count, exts in found:
            inside = os.path.relpath(path, show_path)
            rel = name if inside == "." else "/".join([name, inside])
            already = con.execute(
                "SELECT 1 FROM digital_sources WHERE root_id = ? AND path = ?",
                (root_id, rel)).fetchone() if root_id else None
            if already:
                continue
            copies += 1
            edition = "%s · %d episode%s · %s" % (
                label, count, "" if count == 1 else "s",
                "/".join(sorted(ext.lstrip(".").upper() for ext in exts)))
            print("   %s — %s (%s)" % (title_name, label, edition))
            if args.dry_run:
                continue
            title = con.execute(
                "SELECT id FROM titles WHERE kind = 'tv' AND lower(name) = lower(?)",
                (title_name,)).fetchone()
            title_id = title["id"] if title else con.execute(
                "INSERT INTO titles (name, release_year, kind) VALUES (?, ?, 'tv')",
                (title_name, year)).lastrowid
            item_id = con.execute(
                "INSERT INTO items (title_id, owner_id, format, edition, location,"
                " visibility) VALUES (?, ?, 'digital', ?, ?, 'shared')",
                (title_id, owner_id, edition, name)).lastrowid
            con.execute("INSERT INTO digital_sources (item_id, provider, path, root_id)"
                        " VALUES (?, 'rip', ?, ?)", (item_id, rel, root_id))

    if not args.dry_run:
        con.commit()
    print("tv: %d shows, %d season copies%s"
          % (shows, copies, " (dry run)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

