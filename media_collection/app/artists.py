"""Artists: one row per act, so albums can be browsed by who made them.

Music titles are stored as "Artist — Album", so the act can always be recovered
from the name. Linking happens once (at start-up and after a music import) and the
pages then work from a plain join instead of re-splitting every row.
"""

import argparse
import sys

SEPARATOR = " — "


def split(name):
    """('Backstreet Boys', 'Millennium') for an album title."""
    if SEPARATOR in name:
        artist, album = name.split(SEPARATOR, 1)
        return artist.strip(), album.strip()
    return None, (name or "").strip()


def sort_name(name):
    """'The Beatles' sorts under B, the way a shelf would."""
    for prefix in ("The ", "A ", "An "):
        if name.startswith(prefix):
            return name[len(prefix):] + ", " + prefix.strip()
    return name


def link_all(con):
    """Give every music title an artist, creating the artists as needed."""
    rows = con.execute(
        "SELECT id, name FROM titles WHERE kind = 'music'"
        " AND artist_id IS NULL").fetchall()
    made = linked = 0
    for row in rows:
        artist, _album = split(row["name"])
        if not artist:
            continue
        found = con.execute("SELECT id FROM artists WHERE name = ?",
                            (artist,)).fetchone()
        if found:
            artist_id = found["id"]
        else:
            artist_id = con.execute(
                "INSERT INTO artists (name, sort_name) VALUES (?, ?)",
                (artist, sort_name(artist))).lastrowid
            made += 1
        con.execute("UPDATE titles SET artist_id = ? WHERE id = ?",
                    (artist_id, row["id"]))
        linked += 1
    con.commit()
    return made, linked


def all_artists(con, viewer_id):
    """Every artist with something on the shelf, plus their album counts."""
    return con.execute(
        "SELECT a.*, COUNT(t.id) AS albums,"
        " MIN(t.release_year) AS first_year, MAX(t.release_year) AS last_year"
        " FROM artists a JOIN titles t ON t.artist_id = a.id"
        " WHERE t.kind = 'music'"
        " AND EXISTS (SELECT 1 FROM items i WHERE i.title_id = t.id"
        "             AND (i.visibility = 'shared' OR i.owner_id = ?))"
        " GROUP BY a.id ORDER BY a.sort_name COLLATE NOCASE",
        (viewer_id,)).fetchall()


def get(con, artist_id):
    return con.execute("SELECT * FROM artists WHERE id = ?",
                       (artist_id,)).fetchone()


def titles_for(con, artist_id, viewer_id):
    """The albums of one artist this viewer can see, oldest first."""
    return con.execute(
        "SELECT t.*, COUNT(i.id) AS copies, group_concat(DISTINCT i.format)"
        " AS formats, MIN(i.last_watched_on) AS last_watched_on"
        " FROM titles t JOIN items i ON i.title_id = t.id"
        " WHERE t.artist_id = ? AND t.kind = 'music'"
        " AND (i.visibility = 'shared' OR i.owner_id = ?)"
        " GROUP BY t.id ORDER BY t.release_year, t.name COLLATE NOCASE",
        (artist_id, viewer_id)).fetchall()


def rename(con, artist_id, name):
    con.execute("UPDATE artists SET name = ?, sort_name = ? WHERE id = ?",
                (name, sort_name(name), artist_id))
    con.commit()


def main(argv=None):
    parser = argparse.ArgumentParser(description="link albums to their artist")
    parser.add_argument("db")
    parser.add_argument("--link", action="store_true",
                        help="create missing artists and link the albums")
    args = parser.parse_args(argv)
    import sqlite3

    con = sqlite3.connect(args.db, timeout=10)
    con.row_factory = sqlite3.Row
    made, linked = link_all(con)
    print("artists: %d new, %d album(s) linked" % (made, linked))
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
