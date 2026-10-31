#!/usr/bin/env python3
"""Add ripped films from a folder on the NAS as copies of their titles.

One folder is one film - "Dune (2021)", "American Psycho - Unrated (2000)" - and
every folder becomes a copy owned by someone, with a playback source pointing at
it, so the film can be played without anyone typing a path. The file name decides
the format: 2160p is a 4K disc, 1080p a Blu-ray, 720p/576p/480p a DVD, and
anything else falls back to --format (4K, because that is what most of the shelf
is). Barcode and disc details are left empty on purpose - those come from a
Blu-ray.com export later.

    film_import.py /data/media.db "/media/QNAP/Mitchell's Movies" --owner Mitchell

Re-running is safe: a folder that already has a playback source is left alone.
"""

import argparse
import os
import re
import sqlite3
import unicodedata

VIDEO_EXTENSIONS = (".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".m2ts",
                    ".iso", ".wmv", ".mpg", ".mpeg")
JUNK_FOLDERS = {"@recycle", "#recycle", "@eadir", "system volume information",
                "lost+found"}
EDITION_WORDS = ("edition", "unrated", "remastered", "theatrical", "extended",
                 "special", "director", "cut", "imax", "uncut", "restored",
                 "anniversary", "collector", "criterion", "steelbook")
YEAR_TAIL = re.compile(r"\(\s*(\d{4})\s*\)\s*$")
THREE_D = re.compile(r"\b(3d|hsbs|hou|half-?sbs|half-?ou)\b", re.I)
UHD = re.compile(r"\b(2160p|4k)\b", re.I)
BLURAY = re.compile(r"\b1080p\b", re.I)
DVD = re.compile(r"\b(720p|576[pi]|480[pi]|dvdrip)\b", re.I)
NOTE = ("Ripped copy - format read from the file name; disc details to come "
        "from the Blu-ray.com export.")


def is_junk(name):
    return name.startswith(".") or name.lower() in JUNK_FOLDERS


def parse_folder(folder):
    """'American Psycho - Unrated (2000)' -> ('American Psycho', 'Unrated', 2000)."""
    year = 0
    match = YEAR_TAIL.search(folder)
    name = folder
    if match:
        year = int(match.group(1))
        name = folder[:match.start()].strip()
    parts = [part.strip() for part in name.split(" - ") if part.strip()]
    edition = ""
    if len(parts) > 1 and any(word in parts[-1].lower() for word in EDITION_WORDS):
        edition = parts[-1]
        parts = parts[:-1]
    return " - ".join(parts).strip(), edition, year


def video_files(path):
    """The video files in a folder, biggest first."""
    found = []
    try:
        for name in os.listdir(path):
            full = os.path.join(path, name)
            if os.path.isfile(full) and name.lower().endswith(VIDEO_EXTENSIONS):
                found.append((os.path.getsize(full), name))
    except OSError:
        return []
    return [name for _, name in sorted(found, reverse=True)]


def guess_format(files, default):
    """(format, how) - the disc a file came from, as far as its name says."""
    for name in files:
        if THREE_D.search(name):
            return "bluray_3d", "3D in the file name"
        if UHD.search(name):
            return "uhd_4k", "2160p in the file name"
        if BLURAY.search(name):
            return "bluray", "1080p in the file name"
        if DVD.search(name):
            return "dvd", "SD in the file name"
    return default, "no resolution in the file name"



def resolve_root(con, given, dry_run, label):
    """(root_id, root_path, films_dir) - the media root and the folder to read.

    The media root is the Home Assistant media folder (/media/QNAP); the film
    folders live under it, and their relative path becomes the playback source.
    """
    rows = con.execute("SELECT id, path FROM media_roots"
                       " ORDER BY length(path) DESC").fetchall()
    if not given:
        return None, None, None
    films_dir = given.rstrip("/")

    for row in rows:
        root = row["path"].rstrip("/")
        if films_dir == root or films_dir.startswith(root + "/"):
            return row["id"], root, films_dir

    root_path = os.path.dirname(films_dir)
    if dry_run:
        print("film: %s is outside every media root - would add %s as a root"
              % (films_dir, root_path))
        return None, root_path, films_dir
    if con.execute("SELECT 1 FROM media_roots WHERE label = ?", (label,)).fetchone():
        label = "%s (%s)" % (label, os.path.basename(root_path))
    root_id = con.execute("INSERT INTO media_roots (label, path) VALUES (?, ?)",
                          (label, root_path)).lastrowid
    return root_id, root_path, films_dir
def _fold(text):
    """Loose name key, so 'The Hobbit: An…' and 'The Hobbit - An…' are one film."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"^(the|a|an) ", "", text)


def _catalogue(con):
    """{folded name: [(title id, year), …]} for every film already catalogued."""
    index = {}
    for row in con.execute("SELECT id, name, release_year FROM titles"
                           " WHERE kind = 'movie'"):
        index.setdefault(_fold(row["name"]), []).append(
            (row["id"], row["release_year"]))
    return index


def _title_id(con, index, name, year, owner_id):
    """The catalogue row for this film, made if the shelf has not seen it before.

    A row with the same name and no year is adopted and given the year from the
    folder, so 'RoboCop' with no year becomes 'RoboCop' 1987 instead of a second
    entry sitting next to it.
    """
    rows = index.get(_fold(name), [])
    for title_id, release_year in rows:
        if year and release_year == year:
            return title_id
    if not year:
        if rows:
            return rows[0][0]
    else:
        for title_id, release_year in rows:
            if release_year is None:
                con.execute("UPDATE titles SET release_year = ?"
                            " WHERE id = ? AND release_year IS NULL", (year, title_id))
                index[_fold(name)] = [(tid, year if tid == title_id else ry)
                                      for tid, ry in rows]
                return title_id
    title_id = con.execute(
        "INSERT INTO titles (name, release_year, kind, created_by)"
        " VALUES (?, ?, 'movie', ?)", (name, year or None, owner_id)).lastrowid
    index.setdefault(_fold(name), []).append((title_id, year or None))
    return title_id





def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("db")
    parser.add_argument("root", help="folder holding one folder per film")
    parser.add_argument("--owner", help="who owns these copies (display name)")
    parser.add_argument("--format", default="uhd_4k",
                        help="format when the file name says nothing (default uhd_4k)")
    parser.add_argument("--root-label", default="QNAP",
                        help="label if the media root has to be created")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    con = sqlite3.connect(args.db, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")

    root_id, root_path, films_dir = resolve_root(con, args.root, args.dry_run,
                                                 args.root_label)
    if films_dir is None:
        print("film: nothing to read - pass the folder that holds the films")
        return 0
    if not os.path.isdir(films_dir):
        print("film: %s is not mounted, nothing to do" % films_dir)
        return 0

    if args.owner:
        owner = con.execute("SELECT id FROM users WHERE display_name = ?"
                            " COLLATE NOCASE", (args.owner,)).fetchone()
        if owner is None:
            print("film: no user called %s" % args.owner)
            return 1
    else:
        owner = con.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
        if owner is None:
            print("film: no users yet, nothing to do")
            return 0
    owner_id = owner["id"]

    index = _catalogue(con)
    prefix = os.path.relpath(films_dir, root_path).replace(os.sep, "/")
    films = copies = skipped = adopted = 0
    for folder in sorted(os.listdir(films_dir)):
        folder_path = os.path.join(films_dir, folder)
        if not os.path.isdir(folder_path) or is_junk(folder):
            continue
        files = video_files(folder_path)
        if not files:
            print("film: %s has no video file, skipped" % folder)
            skipped += 1
            continue
        name, edition, year = parse_folder(folder)
        if not name:
            print("film: cannot read a title out of %s, skipped" % folder)
            skipped += 1
            continue
        relative = "/".join(part for part in (prefix, folder) if part and part != ".")
        existing = None
        if root_id:
            existing = con.execute(
                "SELECT d.id, i.owner_id FROM digital_sources d"
                " JOIN items i ON i.id = d.item_id"
                " WHERE d.root_id = ? AND d.path = ?",
                (root_id, relative)).fetchone()
        if existing and existing["owner_id"] == owner_id:
            continue
        films += 1
        fmt, why = guess_format(files, args.format)
        if args.dry_run:
            print("film: %s (%s) -> %s [%s] %s%s"
                  % (name, year or "no year", fmt, why, relative,
                     " (adopt)" if existing else ""))
            copies += 1
            continue

        title_id = _title_id(con, index, name, year, owner_id)
        item_id = con.execute(
            "INSERT INTO items (title_id, owner_id, format, edition, visibility, notes)"
            " VALUES (?, ?, ?, ?, 'shared', ?)",
            (title_id, owner_id, fmt, edition, NOTE)).lastrowid
        if existing:
            con.execute("UPDATE digital_sources SET item_id = ?, notes = ?"
                        " WHERE id = ?", (item_id, why, existing["id"]))
            adopted += 1
        else:
            con.execute("INSERT INTO digital_sources"
                        " (item_id, provider, path, root_id, notes)"
                        " VALUES (?, 'rip', ?, ?, ?)",
                        (item_id, relative, root_id, why))
            copies += 1

    if not args.dry_run:
        con.commit()
    print("film: %d films, %d copies added, %d adopted from another owner,"
          " %d skipped%s" % (films, copies, adopted, skipped,
                             " (dry run)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
