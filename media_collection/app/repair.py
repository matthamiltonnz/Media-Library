#!/usr/bin/env python3
"""Repair a database left half-migrated by an interrupted upgrade.

Runs before migrate.sh on every start. It only touches things this add-on creates:
scratch tables from interrupted table rebuilds, and views that no longer validate
because the table they read was dropped. Idempotent and safe on a healthy database.

usage: repair.py DB
"""
import os
import sqlite3
import sys

SCRATCH = ("digital_sources_new", "items_new", "titles_new")
VIEWS = ("v_digital_sources", "v_shared_library", "v_owner_totals")

DIGITAL_SOURCES = """
CREATE TABLE IF NOT EXISTS digital_sources (
    id          INTEGER PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES items (id) ON DELETE CASCADE,
    provider    TEXT    NOT NULL
                        CHECK (provider IN ('rip', 'jellyfin', 'kodi', 'itunes',
                                            'appletv', 'other')),
    library_id  TEXT,
    external_id TEXT,
    path        TEXT,
    notes       TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (item_id, provider, external_id)
)
"""


def repair(con):
    dropped = []
    for table in SCRATCH:
        exists = con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                             (table,)).fetchone()
        if exists:
            con.execute("DROP TABLE IF EXISTS %s" % table)
            dropped.append(table)

    if not con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table'"
                       " AND name = 'digital_sources'").fetchone():
        con.execute(DIGITAL_SOURCES)
        dropped.append("digital_sources (recreated)")

    fixed_views = []
    for view in VIEWS:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = ?",
                           (view,)).fetchone():
            continue
        try:
            con.execute("SELECT 1 FROM %s LIMIT 0" % view).fetchall()
        except sqlite3.Error:
            con.execute("DROP VIEW IF EXISTS %s" % view)
            fixed_views.append(view)

    con.commit()
    return {"tables": dropped, "views": fixed_views}


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DB_PATH", "/data/media.db")
    if not os.path.exists(path):
        return
    con = sqlite3.connect(path, timeout=30)
    con.execute("PRAGMA foreign_keys = OFF")
    result = repair(con)
    con.close()
    if result["tables"] or result["views"]:
        print("repair: dropped %s; reset views %s"
              % (", ".join(result["tables"]) or "nothing",
                 ", ".join(result["views"]) or "none"))
    else:
        print("repair: database looks consistent")


if __name__ == "__main__":
    main()
