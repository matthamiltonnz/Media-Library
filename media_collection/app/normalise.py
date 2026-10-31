#!/usr/bin/env python3
"""Normalise titles that carry the format in their name.

Blu-ray.com writes "Avatar 3D" / "Akira 4K"; the catalogue keeps format on the
copy, so this flags those copies (3D Blu-ray / 4K Blu-ray) and strips the suffix
from the title, folding it into an existing title of the same name+year when one
is already there. Idempotent - safe to run on every start.

usage: normalise.py DB
"""
import os
import re
import sqlite3
import sys

SUFFIX = re.compile(r"\s+(3D|4K|Ultra HD)$", re.I)


def run(con):
    fixed = renamed = merged = 0
    rows = con.execute("SELECT id, name, release_year FROM titles").fetchall()
    for row in rows:
        match = SUFFIX.search(row["name"])
        if not match:
            continue
        fmt = "bluray_3d" if match.group(1).lower() == "3d" else "uhd_4k"
        cursor = con.execute(
            "UPDATE items SET format = ?, updated_at = datetime('now')"
            " WHERE title_id = ? AND format = 'bluray'", (fmt, row["id"]))
        fixed += cursor.rowcount

        clean = SUFFIX.sub("", row["name"]).strip()
        if not clean:
            continue
        twin = con.execute(
            "SELECT id FROM titles WHERE lower(name) = lower(?)"
            " AND COALESCE(release_year, -1) = COALESCE(?, -1) AND id <> ?",
            (clean, row["release_year"], row["id"])).fetchone()
        if twin:
            con.execute("UPDATE items SET title_id = ? WHERE title_id = ?",
                        (twin["id"], row["id"]))
            con.execute("DELETE FROM titles WHERE id = ?", (row["id"],))
            merged += 1
        else:
            con.execute("UPDATE titles SET name = ? WHERE id = ?", (clean, row["id"]))
            renamed += 1
    con.commit()
    return {"formats": fixed, "renamed": renamed, "merged": merged}


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DB_PATH", "/data/media.db")
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    result = run(con)
    print("normalise: %(formats)d copies flagged, %(renamed)d titles renamed, "
          "%(merged)d folded into existing titles" % result)


if __name__ == "__main__":
    main()
