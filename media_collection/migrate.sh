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
