-- 0012_artists.sql
-- One row per act, so albums can be browsed by who made them. Music titles keep
-- their "Artist — Album" name; titles.artist_id is filled in at start-up.

CREATE TABLE IF NOT EXISTS artists (
    id             INTEGER PRIMARY KEY,
    name           TEXT    NOT NULL,
    sort_name      TEXT,
    musicbrainz_id TEXT,
    country        TEXT,
    formed_year    INTEGER,
    overview       TEXT,
    image_url      TEXT,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_artists_name ON artists (name);

ALTER TABLE titles ADD COLUMN artist_id INTEGER REFERENCES artists (id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_titles_artist ON titles (artist_id);
