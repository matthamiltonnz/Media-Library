"""Merge the bundled catalogue (/app/seed/media.db) into a live database.

Needed because the seed is only copied in on the very first start: an add-on that
was installed before a later import would otherwise never gain those rows.
Idempotent - discs match on barcode, digital copies on title + format + owner.
"""
import os

SEED_ENV = "SEED_DB"


def seed_path():
    path = os.environ.get(SEED_ENV) or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "seed", "media.db")
    return path if os.path.exists(path) else None


def pending_count(con):
    path = seed_path()
    if not path:
        return 0
    con.execute("ATTACH DATABASE ? AS seed", (path,))
    try:
        items = con.execute(
            "SELECT COUNT(*) FROM seed.items s WHERE s.upc_ean IS NOT NULL"
            " AND s.upc_ean NOT IN (SELECT upc_ean FROM items WHERE upc_ean IS NOT NULL)"
        ).fetchone()[0]
        digital = con.execute(
            "SELECT COUNT(*) FROM seed.items s"
            " JOIN seed.titles st ON st.id = s.title_id"
            " JOIN seed.users su ON su.id = s.owner_id"
            " WHERE s.upc_ean IS NULL AND NOT EXISTS ("
            "  SELECT 1 FROM items i JOIN titles t ON t.id = i.title_id"
            "  JOIN users u ON u.id = i.owner_id"
            "  WHERE lower(t.name) = lower(st.name)"
            "    AND COALESCE(t.release_year, -1) = COALESCE(st.release_year, -1)"
            "    AND i.format = s.format AND lower(u.username) = lower(su.username))"
        ).fetchone()[0]
        return items + digital
    except Exception:
        return 0
    finally:
        con.execute("DETACH DATABASE seed")


def _repair_sources(con, seed_item_id, live_item_id):
    """Copy any playback source the seed has that this live copy is missing."""
    added = 0
    for source in con.execute(
            "SELECT provider, library_id, external_id, notes FROM seed.digital_sources"
            " WHERE item_id = ?", (seed_item_id,)).fetchall():
        exists = con.execute(
            "SELECT 1 FROM digital_sources WHERE item_id = ? AND provider = ?"
            " AND COALESCE(library_id, '') = COALESCE(?, '')"
            " AND COALESCE(external_id, '') = COALESCE(?, '')",
            (live_item_id, source["provider"], source["library_id"],
             source["external_id"])).fetchone()
        if exists:
            continue
        con.execute("INSERT INTO digital_sources"
                    " (item_id, provider, library_id, external_id, notes)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (live_item_id, source["provider"], source["library_id"],
                     source["external_id"], source["notes"]))
        added += 1
    return added


def merge(con):
    """Add anything the bundled catalogue has that this library is missing.

    Also repairs playback sources on copies that already exist - useful after an
    interrupted upgrade that left a table empty.
    """
    seed = seed_path()
    if not seed:
        return None
    con.execute("ATTACH DATABASE ? AS seed", (seed,))
    inserted = sources = 0
    try:
        con.execute("DELETE FROM digital_sources WHERE item_id NOT IN (SELECT id FROM items)")
        for user in con.execute(
                "SELECT username, display_name, is_active FROM seed.users").fetchall():
            con.execute("INSERT OR IGNORE INTO users (username, display_name, is_active)"
                        " VALUES (?, ?, ?)", tuple(user))

        rows = con.execute(
            "SELECT s.*, st.name AS t_name, st.release_year AS t_year, st.kind AS t_kind,"
            " st.imdb_id AS t_imdb, su.username AS owner_username"
            " FROM seed.items s"
            " JOIN seed.titles st ON st.id = s.title_id"
            " JOIN seed.users su ON su.id = s.owner_id ORDER BY s.id").fetchall()

        for row in rows:
            if row["upc_ean"]:
                existing = con.execute("SELECT id FROM items WHERE upc_ean = ?",
                                       (row["upc_ean"],)).fetchone()
            else:
                existing = con.execute(
                    "SELECT i.id FROM items i JOIN titles t ON t.id = i.title_id"
                    " JOIN users u ON u.id = i.owner_id"
                    " WHERE lower(t.name) = lower(?)"
                    " AND COALESCE(t.release_year, -1) = COALESCE(?, -1)"
                    " AND i.upc_ean IS NULL AND lower(u.username) = lower(?)",
                    (row["t_name"], row["t_year"], row["owner_username"])).fetchone()
            if existing:
                sources += _repair_sources(con, row["id"], existing["id"])
                continue

            title = con.execute(
                "SELECT id FROM titles WHERE lower(name) = lower(?)"
                " AND COALESCE(release_year, -1) = COALESCE(?, -1)",
                (row["t_name"], row["t_year"])).fetchone()
            title_id = title["id"] if title else con.execute(
                "INSERT INTO titles (name, release_year, kind, imdb_id)"
                " VALUES (?, ?, ?, ?)",
                (row["t_name"], row["t_year"], row["t_kind"], row["t_imdb"])).lastrowid

            owner = con.execute("SELECT id FROM users WHERE lower(username) = lower(?)",
                                (row["owner_username"],)).fetchone()
            item_id = con.execute(
                "INSERT INTO items (title_id, owner_id, format, edition, upc_ean, region,"
                " bluray_com_id, location, condition, visibility, notes)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (title_id, owner["id"], row["format"], row["edition"], row["upc_ean"],
                 row["region"], row["bluray_com_id"], row["location"], row["condition"],
                 row["visibility"], row["notes"])).lastrowid
            inserted += 1
            sources += _repair_sources(con, row["id"], item_id)

        con.commit()
        return {"items": inserted, "digital": sources}
    finally:
        con.execute("DETACH DATABASE seed")


if __name__ == "__main__":
    import sqlite3
    import sys

    db = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DB_PATH", "/data/media.db")
    connection = sqlite3.connect(db)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    waiting = pending_count(connection)
    outcome = merge(connection)          # also repairs missing playback sources
    if outcome and (outcome["items"] or outcome["digital"]):
        print("merge: +%d copies, +%d digital sources" % (outcome["items"], outcome["digital"]))
    else:
        print("merge: bundled catalogue already present (%d pending)" % waiting)
