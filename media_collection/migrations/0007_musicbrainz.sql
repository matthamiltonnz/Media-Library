-- 0007_musicbrainz.sql
-- Albums resolved through MusicBrainz keep the release-group id so the title page
-- can link to the exact release and a later refresh can re-use it.

ALTER TABLE titles ADD COLUMN musicbrainz_id TEXT;

CREATE INDEX IF NOT EXISTS ix_titles_mbid ON titles (musicbrainz_id);
