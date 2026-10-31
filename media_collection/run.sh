#!/usr/bin/env sh
# Home Assistant add-on entrypoint: migrate /data/media.db, then serve.
set -eu

DB=${DB_PATH:-/data/media.db}
APP=${APP_DIR:-/app}
mkdir -p "$(dirname "$DB")"

# First run: adopt a bundled catalogue (owner "Matt", imported from the CSV export).
if [ ! -f "$DB" ] && [ -f "$APP/seed/media.db" ]; then
    echo "seeding $DB from bundled catalogue"
    cp "$APP/seed/media.db" "$DB"
fi

VERSION=${ADDON_VERSION:-}
if [ -z "$VERSION" ] && [ -f "$APP/addon-config.yaml" ]; then
    VERSION=$(sed -n 's/^version: *//p' "$APP/addon-config.yaml" | tr -d '"' | head -1)
fi
echo "media collection ${VERSION:-unknown} starting (db=$DB)"

# drop leftovers from an interrupted upgrade before touching the schema
python3 "$APP/repair.py" "$DB" || echo "repair step skipped"

# keep one copy of the previous database before any schema change
if [ -f "$DB" ]; then
    cp "$DB" "$DB.bak" 2>/dev/null || true
fi

if ! "$APP/migrate.sh" "$DB"; then
    echo "migration failed - see the error above; nothing was half-applied (each"
    echo "migration runs in a transaction) and the add-on retries on restart."
    exit 1
fi

# Adopt anything the bundled catalogue has that this database is missing, then
# fix formats that were embedded in titles ("... 3D" / "... 4K").
DB_PATH="$DB" python3 "$APP/merger.py" "$DB" || echo "merge step skipped"
DB_PATH="$DB" python3 "$APP/normalise.py" "$DB" || echo "normalise step skipped"
DB_PATH="$DB" python3 "$APP/dedupe.py" "$DB" || echo "dedupe step skipped"

# Pick up albums ripped since the last start: one CD copy per album folder plus a rip
# playback source. The root comes from MUSIC_PATH, else the database's music root.
python3 "$APP/music_import.py" "$DB" ${MUSIC_PATH:+"$MUSIC_PATH"} || echo "music scan skipped"

# Same for TV: <Show (Year)>/<Season NN> becomes one title, one copy per season.
python3 "$APP/tv_import.py" "$DB" || echo "tv scan skipped"

# Albums are named "Artist — Album", so their artist can be linked once here and
# the artist pages then read straight from artists/titles.
python3 "$APP/artists.py" "$DB" --link || echo "artist link skipped"

# Mitchell's films come from their own folder, one folder per film. The format is
# read from the file name (2160p -> 4K, 1080p -> Blu-ray); disc details wait for
# the Blu-ray.com export.
FILMS=${FILMS_PATH:-}
[ -n "$FILMS" ] || FILMS="/media/QNAP/Mitchell's Movies"
if [ -d "$FILMS" ]; then
    python3 "$APP/film_import.py" "$DB" "$FILMS" --owner "${FILMS_OWNER:-Mitchell}" \
        || echo "film scan skipped"
fi


echo "starting media collection on :${PORT:-8099} (db=${DB})"
exec python3 "$APP/server.py"
