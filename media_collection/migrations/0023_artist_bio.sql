-- 0023_artist_bio.sql
-- What an act's page says about them.
--
-- artists.overview and artists.image_url have been there since 0012, but
-- overview held MusicBrainz's one-line disambiguation ("Group", "American
-- singer-songwriter and pianist") because MusicBrainz has no biography. A real
-- biography comes from Wikipedia, so this records where the text came from and
-- the "read more" links the artist page shows under it.
ALTER TABLE artists ADD COLUMN wiki_url  TEXT;   -- the en.wikipedia article
ALTER TABLE artists ADD COLUMN link_urls TEXT;   -- JSON [{"label": .., "url": ..}]
ALTER TABLE artists ADD COLUMN bio_at    TEXT;   -- when the bio was fetched
