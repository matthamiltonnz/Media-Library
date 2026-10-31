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


if __name__ == "__main__":
    main()
