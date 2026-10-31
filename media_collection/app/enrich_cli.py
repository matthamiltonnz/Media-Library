#!/usr/bin/env python3
"""Fill missing film years (and IMDb ids) from Wikidata - no API key needed.

usage: enrich_cli.py DB [--limit N]
"""
import argparse
import sqlite3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import metadata  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("db")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row

    rows = con.execute("SELECT name FROM titles WHERE release_year IS NULL OR imdb_id IS NULL"
                       " ORDER BY name").fetchall()
    names = sorted({metadata.clean_title(r["name"]) for r in rows})
    if args.limit:
        names = names[:args.limit]
    print("%d titles to look up" % len(names), flush=True)

    found = {}
    chosen = metadata.search_wikidata(names, workers=1, pause=0.5)
    print("matched %d/%d titles to a Wikidata item" % (len(chosen), len(names)), flush=True)
    entities = metadata.fetch_entities(list(chosen.values()))
    for name, qid in chosen.items():
        found[name] = entities.get(qid, (None, None))
    print("entities with data: %d" % sum(1 for v in found.values() if v[0] or v[1]), flush=True)

    years = imdbs = 0
    for row in con.execute("SELECT id, name, release_year, imdb_id FROM titles").fetchall():
        year, imdb = found.get(metadata.clean_title(row["name"]), (None, None))
        if year and not row["release_year"]:
            con.execute("UPDATE titles SET release_year = ? WHERE id = ?", (year, row["id"]))
            years += 1
        if imdb and not row["imdb_id"]:
            try:
                con.execute("UPDATE titles SET imdb_id = ? WHERE id = ?", (imdb, row["id"]))
                imdbs += 1
            except sqlite3.IntegrityError:      # older schema: one title per IMDb id
                con.rollback()
    con.commit()

    missing = con.execute("SELECT COUNT(*) FROM titles WHERE release_year IS NULL").fetchone()[0]
    print("years set: %d, imdb ids set: %d, still missing a year: %d"
          % (years, imdbs, missing))


if __name__ == "__main__":
    main()
