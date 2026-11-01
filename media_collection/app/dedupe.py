#!/usr/bin/env python3
"""Fold duplicate title rows that describe the same film.

The disc export has no year and the iTunes import does, so the same film can end up
as "... Endgame" (no year) and "... Endgame" (2019). This merges such pairs - only
when the years cannot contradict each other (equal, or one of them unknown) - and
moves every copy across.

usage: dedupe.py DB [--dry-run]
"""
import os
import re
import sqlite3
import sys

METADATA = ["overview", "poster_url", "runtime_minutes", "genres", "rating",
            "imdb_id", "imdb_id", "metadata_source"]


def key(name):
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def groups(con):
    """[(keeper_row, [duplicate_rows])] for titles that are safe to fold."""
    buckets = {}
    for row in con.execute("SELECT id, name, release_year FROM titles ORDER BY id"):
        buckets.setdefault(key(row["name"]), []).append(row)

    found = []
    for members in buckets.values():
        if len(members) < 2:
            continue
        years = {m["release_year"] for m in members if m["release_year"]}
        if len(years) > 1:
            continue                     # same name, different films - leave alone
        keeper = next((m for m in members if m["release_year"]), members[0])
        found.append((keeper, [m for m in members if m["id"] != keeper["id"]]))
    return found


def merge(con):
    folded = 0
    for keeper, others in groups(con):
        for other in others:
            con.execute("UPDATE items SET title_id = ? WHERE title_id = ?",
                        (keeper["id"], other["id"]))
            # keep any metadata the duplicate had and the keeper lacks
            donor = con.execute("SELECT * FROM titles WHERE id = ?", (other["id"],)).fetchone()
            for column in ("overview", "poster_url", "runtime_minutes", "genres",
                           "rating", "imdb_id", "tmdb_id", "metadata_source"):
                if donor[column] and not con.execute(
                        "SELECT %s FROM titles WHERE id = ?" % column,
                        (keeper["id"],)).fetchone()[0]:
                    con.execute("UPDATE titles SET %s = ? WHERE id = ?" % column,
                                (donor[column], keeper["id"]))
            con.execute("DELETE FROM titles WHERE id = ?", (other["id"],))
            folded += 1
    con.commit()
    return folded


def split_mixed(con):
    """A copy that carries both a store source and a file is really two copies.

    An iTunes purchase and the rip of the same album are different things on the
    shelf, so the file moves onto its own "ripped" copy and the purchase stays on
    a "digital" one.
    """
    made = 0
    rows = con.execute(
        "SELECT i.id, i.title_id, i.owner_id, i.format, i.visibility, i.edition,"
        "       SUM(d.provider = 'rip') AS rips,"
        "       SUM(d.provider <> 'rip') AS stores"
        "  FROM items i JOIN digital_sources d ON d.item_id = i.id"
        " GROUP BY i.id HAVING rips > 0 AND stores > 0").fetchall()
    for row in rows:
        want = "digital" if row["format"] == "ripped" else "ripped"
        clause = "provider <> 'rip'" if want == "digital" else "provider = 'rip'"
        new_id = con.execute(
            "INSERT INTO items (title_id, owner_id, format, edition, visibility)"
            " VALUES (?, ?, ?, ?, ?)",
            (row["title_id"], row["owner_id"], want, row["edition"],
             row["visibility"])).lastrowid
        con.execute("UPDATE digital_sources SET item_id = ?"
                    " WHERE item_id = ? AND " + clause, (new_id, row["id"]))
        made += 1
    con.commit()
    return made


def copies(con):
    """Fold repeated copies of the same thing into one.

    Two shapes of duplicate build up when a rip import runs on every start: the
    same file re-imported (same digital_sources.path), and the copy the import
    left behind after moving its source to a newer row. Barcoded copies are never
    folded — two discs with different barcodes are two discs.
    """
    folded = 0
    rows = con.execute(
        "SELECT d.id AS source_id, d.item_id, d.root_id, d.path"
        "  FROM digital_sources d JOIN items i ON i.id = d.item_id"
        " WHERE d.path IS NOT NULL"
        " ORDER BY i.id, d.id").fetchall()
    keep = {}
    for row in rows:                     # the same file imported twice
        key = (row["root_id"] or 0, row["path"])
        first = keep.setdefault(key, row["item_id"])
        if row["item_id"] == first:
            continue
        con.execute("UPDATE digital_sources SET item_id = ? WHERE id = ?",
                    (first, row["source_id"]))
        if _drop_if_empty(con, row["item_id"]):
            folded += 1

    groups = con.execute(
        "SELECT title_id, owner_id, format, COALESCE(edition, '') AS edition,"
        "       visibility, group_concat(id) AS ids"
        "  FROM items WHERE upc_ean IS NULL"
        " GROUP BY title_id, owner_id, format, COALESCE(edition, ''), visibility"
        " HAVING COUNT(*) > 1").fetchall()
    for group in groups:                 # the same copy added twice
        ids = [int(i) for i in group["ids"].split(",")]
        with_source = con.execute(
            "SELECT item_id FROM digital_sources WHERE item_id IN (%s) LIMIT 1"
            % ", ".join("?" * len(ids)), ids).fetchone()
        keeper = with_source["item_id"] if with_source else min(ids)
        for item_id in ids:
            if item_id == keeper:
                continue
            con.execute("UPDATE digital_sources SET item_id = ? WHERE item_id = ?",
                        (keeper, item_id))
            if _drop_if_empty(con, item_id):
                folded += 1
    con.commit()
    return folded


def _drop_if_empty(con, item_id):
    """Remove a copy that no longer has a file of its own behind it."""
    if con.execute("SELECT 1 FROM digital_sources WHERE item_id = ? LIMIT 1",
                   (item_id,)).fetchone():
        return False
    con.execute("DELETE FROM jellyfin_links WHERE item_id = ?", (item_id,))
    con.execute("DELETE FROM items WHERE id = ?", (item_id,))
    return True


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DB_PATH", "/data/media.db")
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    if "--dry-run" in sys.argv:
        for keeper, others in groups(con):
            print("%-45s <- %s" % (keeper["name"][:45],
                                   ", ".join("#%d" % o["id"] for o in others)))
        return
    print("dedupe: %d duplicate title(s) folded" % merge(con))
    print("dedupe: %d mixed copy(s) split" % split_mixed(con))
    print("dedupe: %d duplicate copy(s) folded" % copies(con))


if __name__ == "__main__":
    main()
