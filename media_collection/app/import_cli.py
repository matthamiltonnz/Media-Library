#!/usr/bin/env python3
"""Import a CSV straight into a database, without the web UI.

usage: import_cli.py DB CSV OWNER [--name "Display Name"] [--visibility shared]
                      [--map title=Column --map upc=UPC]

Reuses the same staging + commit code as the app, so duplicates are reported the
same way and nothing is committed unless its status is 'ok'.
"""
import argparse
import json
import sqlite3
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importer  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("db")
    parser.add_argument("csv")
    parser.add_argument("owner", help="username of the owner (created if missing)")
    parser.add_argument("--name", default=None, help="display name for a new owner")
    parser.add_argument("--visibility", default="shared", choices=("shared", "private"))
    parser.add_argument("--map", action="append", default=[], metavar="FIELD=HEADER")
    args = parser.parse_args()

    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")

    owner = con.execute("SELECT id FROM users WHERE lower(username) = lower(?)",
                        (args.owner,)).fetchone()
    if owner is None:      # only create the profile when nobody matches by name
        con.execute("INSERT INTO users (username, display_name) VALUES (?, ?)",
                    (args.owner, args.name or args.owner.title()))
        con.commit()
        owner = con.execute("SELECT id FROM users WHERE lower(username) = lower(?)",
                            (args.owner,)).fetchone()

    with open(args.csv, "rb") as fh:
        raw = fh.read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8-sig", errors="replace")

    headers, records = importer.parse_csv(text)
    if not records:
        sys.exit("no rows found in %s" % args.csv)

    mapping = importer.guess_mapping(headers)
    for override in args.map:
        field, _, header = override.partition("=")
        if field not in dict(importer.FIELDS):
            sys.exit("unknown field %r (valid: %s)"
                     % (field, ", ".join(f for f, _ in importer.FIELDS)))
        if header not in headers:
            sys.exit("column %r not in CSV (have: %s)" % (header, ", ".join(headers)))
        mapping[field] = header

    cur = con.execute("INSERT INTO imports (filename, raw_csv, created_by) VALUES (?, ?, ?)",
                      (os.path.basename(args.csv), text, owner["id"]))
    con.commit()
    imp = con.execute("SELECT * FROM imports WHERE id = ?", (cur.lastrowid,)).fetchone()

    importer.stage(con, imp, mapping)
    counts = {r["status"]: r["n"] for r in con.execute(
        "SELECT status, COUNT(*) AS n FROM import_rows WHERE import_id = ? GROUP BY status",
        (imp["id"],))}
    result = importer.commit(con, imp, owner["id"], args.visibility)

    print("mapping : %s" % json.dumps(mapping, sort_keys=True))
    print("staged  : %d rows  %s" % (len(records), counts))
    if result:
        print("imported: %d ok, %d failed" % result)
    for row in con.execute(
            "SELECT line_no, title, message FROM import_rows WHERE import_id = ?"
            " AND status != 'ok' ORDER BY line_no LIMIT 10", (imp["id"],)):
        print("  line %-4d %-45s %s" % (row["line_no"], (row["title"] or "")[:45],
                                       row["message"]))


if __name__ == "__main__":
    main()
