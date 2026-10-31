-- 0003_metadata.sql
-- Per-title metadata (populated on demand from the detail page) plus a
-- last-watched date on each copy.

PRAGMA foreign_keys = ON;

ALTER TABLE titles ADD COLUMN overview           TEXT;
ALTER TABLE titles ADD COLUMN poster_url         TEXT;
ALTER TABLE titles ADD COLUMN runtime_minutes    INTEGER;
ALTER TABLE titles ADD COLUMN genres             TEXT;
ALTER TABLE titles ADD COLUMN rating             TEXT;   -- 'PG-13', 'M', 'G'...
ALTER TABLE titles ADD COLUMN metadata_source    TEXT;   -- itunes / wikidata
ALTER TABLE titles ADD COLUMN metadata_updated_at TEXT;

ALTER TABLE items ADD COLUMN last_watched_on TEXT;       -- ISO-8601 date

CREATE INDEX ix_items_last_watched ON items (last_watched_on);

-- Editions of the same film (3D + 4K, for example) legitimately share an IMDb id.
DROP INDEX IF EXISTS ux_titles_imdb;
CREATE INDEX ix_titles_imdb ON titles (imdb_id);
