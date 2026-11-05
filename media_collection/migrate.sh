#!/bin/sh
# Apply every db/migrations/*.sql that has not run yet.
# usage: ./db/migrate.sh [path/to/database.db]
set -eu

DB=${1:-${DB_PATH:-db/media.db}}
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

command -v sqlite3 >/dev/null 2>&1 || { echo "sqlite3 not found" >&2; exit 1; }

sqlite3 "$DB" "PRAGMA journal_mode=WAL;
               CREATE TABLE IF NOT EXISTS schema_migrations (
                   version    TEXT PRIMARY KEY,
                   applied_at TEXT NOT NULL DEFAULT (datetime('now'))
               );" >/dev/null

# The baseline describes the whole schema in one file. A database that already
# has tables was built by an earlier version (or by the 33 files this replaced),
# so it is recorded as having run the baseline rather than rebuilt under itself
# - its CREATEs would collide with everything already there.
BASE=0001_schema
seen=$(sqlite3 "$DB" "SELECT 1 FROM sqlite_master
                      WHERE type = 'table' AND name = 'titles';")
done_=$(sqlite3 "$DB" "SELECT 1 FROM schema_migrations WHERE version = '$BASE';")
if [ -n "$seen" ] && [ -z "$done_" ]; then
    sqlite3 "$DB" "INSERT INTO schema_migrations (version) VALUES ('$BASE');"
    echo "baseline $BASE (this database already had a schema)"
fi

for f in "$HERE"/migrations/*.sql; do
    [ -e "$f" ] || continue
    v=$(basename "$f" .sql)
    applied=$(sqlite3 "$DB" "SELECT 1 FROM schema_migrations WHERE version = '$v';")
    if [ -n "$applied" ]; then
        echo "skip    $v"
        continue
    fi
    echo "apply   $v"
    sqlite3 -bail "$DB" "PRAGMA foreign_keys = ON;" ".read '$f'" \
        "INSERT INTO schema_migrations (version) VALUES ('$v');"
done

echo "done    $DB"
