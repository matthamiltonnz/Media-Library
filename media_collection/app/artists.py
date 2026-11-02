"""Artists: one row per act, so albums can be browsed by who made them.

Music titles are stored as "Artist — Album", so the act can always be recovered
from the name. Linking happens once (at start-up and after a music import) and the
pages then work from a plain join instead of re-splitting every row.
"""

import argparse
import sys
import threading

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


def link(con, title_id, name, mbid=""):
    """Give one title its artist, creating the artist row when it is new.

    The artist name comes from the form, so a new album can be filed under the
    right act straight away instead of waiting for a later link_all pass. An
    empty name leaves the title alone (link_all will guess from "Artist — Album").
    """
    name = (name or "").strip()
    if not name:
        return None
    mbid = (mbid or "").strip() or None
    found = con.execute("SELECT id FROM artists WHERE name = ?",
                        (name,)).fetchone()
    if found:
        artist_id = found["id"]
        if mbid:
            con.execute("UPDATE artists SET musicbrainz_id = ?"
                        " WHERE id = ? AND musicbrainz_id IS NULL",
                        (mbid, artist_id))
    else:
        artist_id = con.execute(
            "INSERT INTO artists (name, sort_name, musicbrainz_id)"
            " VALUES (?, ?, ?)", (name, sort_name(name), mbid)).lastrowid
    con.execute("UPDATE titles SET artist_id = ? WHERE id = ?",
                (artist_id, title_id))
    return artist_id


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
    """Every artist with something on the shelf, plus their album counts and art."""
    return con.execute(
        "SELECT a.*, COUNT(t.id) AS albums,"
        " MIN(t.release_year) AS first_year, MAX(t.release_year) AS last_year,"
        " (SELECT l.jf_id FROM titles t2"
        "    JOIN items i2 ON i2.title_id = t2.id"
        "    JOIN jellyfin_links l ON l.item_id = i2.id"
        "   WHERE t2.artist_id = a.id ORDER BY t2.id LIMIT 1) AS jf_id,"
        " (SELECT t3.poster_url FROM titles t3 JOIN items i3 ON i3.title_id = t3.id"
        "   WHERE t3.artist_id = a.id AND t3.poster_url IS NOT NULL"
        "   ORDER BY t3.id LIMIT 1) AS poster_url"
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
        " AS formats, MIN(i.last_watched_on) AS last_watched_on,"
        " (SELECT l.jf_id FROM jellyfin_links l JOIN items i2 ON i2.id = l.item_id"
        "   WHERE i2.title_id = t.id ORDER BY l.jf_id LIMIT 1) AS jf_id"
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


def enrich(con, limit=None):
    """Fill in what MusicBrainz knows about each act: id, country, start year.

    It used to look only at artists with *no* MusicBrainz id, which on this
    shelf meant six rows and, when their lookups missed, a job that filled
    nothing. It now also tops up the blanks on acts that already have an id.
    """
    import metadata

    rows = con.execute(
        "SELECT id, name, musicbrainz_id, country, formed_year FROM artists"
        " WHERE musicbrainz_id IS NULL OR country IS NULL"
        "    OR formed_year IS NULL"
        " ORDER BY name").fetchall()
    filled = 0
    for row in rows:
        if limit is not None and filled >= limit:
            break
        mbid = row["musicbrainz_id"]
        try:
            if not mbid:
                looked = metadata.musicbrainz_artist(row["name"])
                if not looked:
                    continue
                mbid = looked.get("mbid") or looked.get("id")
                if not mbid:
                    continue
            details = metadata.musicbrainz_artist_by_id(mbid) or {}
        except Exception:
            continue
        updates, values = [], []
        if not row["musicbrainz_id"]:
            updates.append("musicbrainz_id = ?")
            values.append(mbid)
        if not row["country"] and details.get("country"):
            updates.append("country = ?")
            values.append(details["country"])
        if not row["formed_year"] and details.get("year"):
            updates.append("formed_year = ?")
            values.append(details["year"])
        if updates:
            con.execute("UPDATE artists SET %s WHERE id = ?"
                        % ", ".join(updates), values + [row["id"]])
            filled += 1
        con.commit()
    return filled


_STATE = {"running": False, "filled": 0, "at": None}


def status():
    return dict(_STATE)


def summary():
    """One line for the admin page: what the last fill managed to do."""
    state = status()
    if state["running"]:
        return "Filling artist details from MusicBrainz..."
    if state["at"]:
        return ("Filled %d artist(s) from MusicBrainz on %s."
                % (state["filled"], state["at"]))
    return ""


def start(db_path, limit=None):
    """Fill artist details in the background - MusicBrainz allows one call a second."""
    if _STATE["running"]:
        return False
    _STATE.update(running=True, filled=0)

    def _run():
        import datetime
        import sqlite3

        con = sqlite3.connect(db_path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            filled = enrich(con, limit)
            _STATE.update(filled=filled,
                          at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
        except Exception as exc:               # keep the app serving
            print("artists: %s" % exc, file=sys.stderr)
        finally:
            con.close()
            _STATE["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return True


if __name__ == "__main__":
    sys.exit(main())
