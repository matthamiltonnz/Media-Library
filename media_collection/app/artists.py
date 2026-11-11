"""Artists: one row per act, so albums can be browsed by who made them.

Music titles are stored as "Artist — Album", so the act can always be recovered
from the name. Linking happens once (at start-up and after a music import) and the
pages then work from a plain join instead of re-splitting every row.
"""

import argparse
import json
import re
import sys
import threading

import sorting

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
    sorting.register(con)      # NATSORT is per connection; see sorting.py
    return con.execute(
        "SELECT a.*, COUNT(t.id) AS albums,"
        " MIN(t.release_year) AS first_year, MAX(t.release_year) AS last_year,"
        " (SELECT group_concat(DISTINCT i4.owner_id) FROM titles t4"
        "    JOIN items i4 ON i4.title_id = t4.id"
        "   WHERE t4.artist_id = a.id) AS owner_ids,"
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
        " GROUP BY a.id ORDER BY a.sort_name COLLATE NATSORT",
        (viewer_id,)).fetchall()


def get(con, artist_id):
    return con.execute("SELECT * FROM artists WHERE id = ?",
                       (artist_id,)).fetchone()


def matching(con, viewer_id, query, owner=None):
    """The acts whose name matches `query`, for the catalogue's results.

    `visible_clause` already finds their albums when you search an act's name,
    but it shows the records, not the act. This is the row the catalogue turns
    into a card that opens the artist's page.
    """
    needle = (query or "").strip().lower()
    if not needle:
        return []
    found = []
    for row in all_artists(con, viewer_id):
        if needle not in (row["name"] or "").lower():
            continue
        if owner and str(owner) not in (row["owner_ids"] or "").split(","):
            continue
        found.append(row)
    return found


def titles_for(con, artist_id, viewer_id):
    """The albums of one artist this viewer can see, oldest first."""
    sorting.register(con)      # NATSORT is per connection; see sorting.py
    return con.execute(
        "SELECT t.*, COUNT(i.id) AS copies, group_concat(DISTINCT i.format)"
        " AS formats, MIN(i.last_watched_on) AS last_watched_on,"
        " (SELECT l.jf_id FROM jellyfin_links l JOIN items i2 ON i2.id = l.item_id"
        "   WHERE i2.title_id = t.id ORDER BY l.jf_id LIMIT 1) AS jf_id"
        " FROM titles t JOIN items i ON i.title_id = t.id"
        " WHERE t.artist_id = ? AND t.kind = 'music'"
        " AND (i.visibility = 'shared' OR i.owner_id = ?)"
        " GROUP BY t.id ORDER BY t.release_year,"
        " COALESCE(NULLIF(t.sort_name, ''), t.name) COLLATE NATSORT,"
        " t.name COLLATE NATSORT",
        (artist_id, viewer_id)).fetchall()


MBID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}"
                   r"-[0-9a-f]{12}$")


def set_mbid(con, artist_id, value):
    """Point an act at a MusicBrainz id, or clear it. Returns (ok, message).

    The id is what the links and the "read more" row hang off, so a wrong or
    missing one leaves an act with a MusicBrainz link to nothing. Setting it
    here also re-reads the id's own record - country, start year, kind - because
    those were the fields the failed lookup left blank or filled from the wrong
    act. The derived links are dropped (not the biography: that was looked up by
    name), so *Fetch details* rebuilds them against the id that is now stored.
    """
    import metadata

    value = (value or "").strip().lower()
    if value and not MBID.match(value):
        return False, ("That is not a MusicBrainz id - it is the 36-character"
                       " uuid from the artist's own MusicBrainz URL.")
    if not value:
        con.execute("UPDATE artists SET musicbrainz_id = NULL, country = NULL,"
                    " formed_year = NULL, kind = NULL, overview = NULL,"
                    " wiki_url = NULL, link_urls = NULL, image_url = NULL,"
                    " image_at = NULL, image_candidates = NULL,"
                    " bio_at = NULL WHERE id = ?", (artist_id,))
        con.commit()
        return True, "MusicBrainz id cleared."
    try:
        details = metadata.musicbrainz_artist_by_id(value) or {}
    except Exception as exc:                 # offline, throttled, whatever
        details = {}
        print("artists: MusicBrainz said %s" % exc)
    # Everything derived from the *old* id goes with it: the biography, the
    # portrait and the remembered pictures all belong to whichever act was
    # there before, so keeping them shows one band with another's face. A save
    # of the id already stored is left alone - the box holds the current id, so
    # pressing Save again must not throw away a portrait somebody chose.
    row = con.execute("SELECT musicbrainz_id FROM artists WHERE id = ?",
                      (artist_id,)).fetchone()
    if row is not None and (row[0] or "") == value:
        con.execute(
            "UPDATE artists SET country = COALESCE(?, country),"
            " formed_year = COALESCE(?, formed_year),"
            " kind = COALESCE(?, kind) WHERE id = ?",
            (details.get("country") or None, details.get("year") or None,
             details.get("kind") or None, artist_id))
    else:
        con.execute(
            "UPDATE artists SET musicbrainz_id = ?, country = ?, formed_year = ?,"
            " kind = ?, overview = NULL, wiki_url = NULL, link_urls = NULL,"
            " image_url = NULL, image_at = NULL, image_candidates = NULL,"
            " bio_at = NULL WHERE id = ?",
            (value, details.get("country") or None, details.get("year") or None,
             details.get("kind") or None, artist_id))
    con.commit()
    if not details:
        return True, "Saved, but MusicBrainz did not answer for that id."
    said = ", ".join(filter(None, [details.get("name"),
                                   details.get("kind"),
                                   details.get("country"),
                                   str(details.get("year") or "")]))
    return True, "Saved: %s." % (said or value)


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


def _fill_from_musicbrainz(con, row):
    """The id, country, start year and kind for one act.

    Returns 1 if anything landed. The act is looked up by *name* only when it
    has no id; otherwise the id's own record is read and the blanks are topped
    up, so a wrong country or year from an old lookup is never overwritten.
    """
    import metadata

    mbid = row["musicbrainz_id"]
    try:
        if not mbid:
            looked = metadata.musicbrainz_artist(row["name"])
            if not looked:
                return 0
            mbid = looked.get("mbid") or looked.get("id")
            if not mbid:
                return 0
        details = metadata.musicbrainz_artist_by_id(mbid) or {}
    except Exception:
        return 0
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
    if not row["kind"] and details.get("kind"):
        updates.append("kind = ?")
        values.append(details["kind"])
    if not updates:
        return 0
    con.execute("UPDATE artists SET %s WHERE id = ?"
                % ", ".join(updates), values + [row["id"]])
    con.commit()
    return 1


def enrich(con, limit=None):
    """Fill in what MusicBrainz knows about each act: id, country, start year.

    It used to look only at artists with *no* MusicBrainz id, which on this
    shelf meant six rows and, when their lookups missed, a job that filled
    nothing. It now also tops up the blanks on acts that already have an id.
    """
    rows = con.execute(
        "SELECT id, name, musicbrainz_id, country, formed_year, kind FROM artists"
        " WHERE musicbrainz_id IS NULL OR country IS NULL"
        "    OR formed_year IS NULL OR kind IS NULL"
        " ORDER BY name").fetchall()
    filled = 0
    _STATE.update(running=True, done=0, total=len(rows), filled=0)
    for seen, row in enumerate(rows, 1):
        _STATE["done"] = seen
        if limit is not None and filled >= limit:
            break
        filled += _fill_from_musicbrainz(con, row)
        _STATE["filled"] = filled
    # a biography comes from Wikipedia, not MusicBrainz - and only once the
    # MusicBrainz id above is there, because the links list it
    filled += fill_bio(con)
    # and a portrait, for the acts that have none - the artist wall shows it
    fill_images(con)
    _STATE["running"] = False
    return filled


def enrich_one(con, artist_id):
    """Details for a single act - what adding a title uses for a new one.

    The same two lookups the job runs, but only for what is missing: a second
    album by an act already on the shelf costs one row read.
    """
    row = con.execute(
        "SELECT id, name, musicbrainz_id, country, formed_year, kind, bio_at,"
        "       image_url, image_at"
        "  FROM artists WHERE id = ?", (artist_id,)).fetchone()
    if row is None:
        return 0
    made = 0
    if not (row["musicbrainz_id"] and row["country"] and row["formed_year"]
            and row["kind"]):
        made += _fill_from_musicbrainz(con, row)
    if row["bio_at"] is None:
        made += fill_bio(con, artist_id=artist_id)
    if not (row["image_url"] or "").strip():
        made += fill_images(con, artist_id=artist_id)
    return made


def fill_bio(con, artist_id=None, limit=None):
    """Give acts a real biography, a portrait and "read more" links.

    MusicBrainz has none of that - the "overview" it can offer is a
    disambiguation line ("Group"), which is what the column held until now - so
    the text comes from Wikipedia (metadata.artist_bio). An act that comes back
    empty is stamped as well: without that, every run would ask Wikipedia about
    the same handful of acts that have no article. The artist page's own button
    passes an id and always asks again, so a wrong match can be retried.
    """
    import json

    import metadata

    if artist_id is None:
        rows = con.execute(
            "SELECT id, name, musicbrainz_id FROM artists"
            " WHERE bio_at IS NULL ORDER BY name").fetchall()
    else:
        rows = con.execute(
            "SELECT id, name, musicbrainz_id FROM artists WHERE id = ?",
            (artist_id,)).fetchall()
    made = 0
    for row in rows:
        if limit is not None and made >= limit:
            break
        try:
            found = metadata.artist_bio(row["name"], row["musicbrainz_id"])
        except Exception:
            continue
        if found:
            # a re-fetch must not wipe a portrait it cannot replace: the
            # summary of a second look often has no lead image, and this used
            # to write NULL over a picture that came from Wikidata
            con.execute(
                "UPDATE artists SET overview = ?, wiki_url = ?,"
                " link_urls = ?, bio_at = datetime('now'),"
                " image_url = COALESCE(NULLIF(?, ''), image_url)"
                " WHERE id = ?",
                (found["overview"], found["wiki_url"],
                 json.dumps(found["links"]), found["image_url"], row["id"]))
            made += 1
            _STATE["bios"] = (_STATE.get("bios") or 0) + 1
        else:
            con.execute("UPDATE artists SET bio_at = datetime('now')"
                        " WHERE id = ?", (row["id"],))
        con.commit()
    return made


def fill_images(con, artist_id=None, limit=None):
    """Give acts without a portrait one - Wikipedia's lead image, then Wikidata.

    The artist wall used to show the first album's sleeve for every act, so the
    photograph the biography lookup already stored was never seen. `image_at`
    is the stamp that makes this run once per act: an act nobody has a
    photograph of is recorded as asked rather than re-asked on every refresh -
    the same rule `fill_bio` follows.
    """
    import metadata
    where = ("WHERE (image_url IS NULL OR TRIM(image_url) = '')"
             " AND image_at IS NULL")
    if artist_id is None:
        rows = con.execute(
            "SELECT id, name, musicbrainz_id, wiki_url FROM artists "
            + where + " ORDER BY name").fetchall()
    else:
        rows = con.execute(
            "SELECT id, name, musicbrainz_id, wiki_url FROM artists "
            + where + " AND id = ?", (artist_id,)).fetchall()
    made = 0
    for row in rows:
        if limit is not None and made >= limit:
            break
        try:
            found = metadata.artist_image(row["name"], row["musicbrainz_id"],
                                          row["wiki_url"])
        except Exception as exc:            # a lookup must not stop the run
            print("artists: %s" % exc, file=sys.stderr)
            found = ""
        if found:
            con.execute("UPDATE artists SET image_url = ?,"
                        " image_at = datetime('now') WHERE id = ?",
                        (found, row["id"]))
            made += 1
            _STATE["images"] = (_STATE.get("images") or 0) + 1
        else:
            con.execute("UPDATE artists SET image_at = datetime('now')"
                        " WHERE id = ?", (row["id"],))
        con.commit()
    return made



_STATE = {"running": False, "filled": 0, "bios": 0, "images": 0, "at": None}



def image_choices(con, artist_id):
    """The pictures remembered for an act, newest last."""
    row = con.execute("SELECT image_candidates FROM artists WHERE id = ?",
                      (artist_id,)).fetchone()
    try:
        kept = json.loads(row[0]) if row and row[0] else []
    except (TypeError, ValueError):
        kept = []
    return [item for item in kept
            if isinstance(item, dict) and item.get("url")]


def remember_images(con, artist_id, found, keep=12):
    """Add pictures to the act's list, newest last and without duplicates."""
    kept = image_choices(con, artist_id)
    urls = {item["url"] for item in kept}
    for item in found or []:
        url = (item.get("url") or "").strip()
        if url and url not in urls:
            urls.add(url)
            kept.append({"source": item.get("source") or "",
                         "url": url,
                         "label": item.get("label") or ""})
    kept = kept[-keep:]
    con.execute("UPDATE artists SET image_candidates = ? WHERE id = ?",
                (json.dumps(kept), artist_id))
    con.commit()
    return kept


def set_image(con, artist_id, url):
    """Point the act's portrait at one picture."""
    con.execute("UPDATE artists SET image_url = ?,"
                " image_at = datetime('now') WHERE id = ?",
                ((url or "").strip(), artist_id))
    con.commit()


def forget_image(con, artist_id, url):
    """Drop one picture from the list; unset it when it is the one on show."""
    url = (url or "").strip()
    kept = [item for item in image_choices(con, artist_id)
            if item["url"] != url]
    con.execute("UPDATE artists SET image_candidates = ? WHERE id = ?",
                (json.dumps(kept), artist_id))
    row = con.execute("SELECT image_url FROM artists WHERE id = ?",
                      (artist_id,)).fetchone()
    if row and (row[0] or "").strip() == url:
        # no picture at all again, and no stamp - a refresh may find another
        con.execute("UPDATE artists SET image_url = NULL, image_at = NULL"
                    " WHERE id = ?", (artist_id,))
    con.commit()
    return kept


def refresh_art(con, artist_id):
    """Search for pictures of an act, remember them, and return (found, note)."""
    import metadata
    row = con.execute(
        "SELECT id, name, musicbrainz_id, wiki_url FROM artists WHERE id = ?",
        (artist_id,)).fetchone()
    if row is None:
        return [], "No such artist."
    found, note = metadata.artist_cover_search(row["name"],
                                               row["musicbrainz_id"] or "",
                                               row["wiki_url"] or "")
    remember_images(con, artist_id, found)
    return found, note

def status():
    return dict(_STATE)


def summary():
    """One line for the admin page: what the last fill managed to do."""
    state = status()
    if state["running"]:
        if state.get("total"):
            return ("Filling artist details - %d of %d acts, %d filled."
                    % (state.get("done") or 0, state["total"],
                       state.get("filled") or 0))
        return "Filling artist details from MusicBrainz..."
    if state["at"]:
        if state.get("bios") or state.get("images"):
            return ("Filled %d artist(s) from MusicBrainz, gave %d a Wikipedia"
                    " biography and %d a portrait on %s."
                    % (state["filled"], state.get("bios") or 0,
                       state.get("images") or 0, state["at"]))
        return ("Filled %d artist(s) from MusicBrainz on %s."
                % (state["filled"], state["at"]))
    return ""


def start(db_path, limit=None):
    """Fill artist details in the background - MusicBrainz allows one call a second."""
    if _STATE["running"]:
        return False
    _STATE.update(running=True, filled=0, bios=0, images=0)

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
