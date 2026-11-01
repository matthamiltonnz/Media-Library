-- 0014_cover_art.sql
-- Which image a title shows. Lookups keep offering posters (Wikipedia, IMDB via
-- Wikidata, MusicBrainz covers) and the household may prefer the Jellyfin still
-- or one of the older posters, so every url seen is remembered and the choice is
-- stored in art_source (NULL = automatic: Jellyfin first, then the poster).

ALTER TABLE titles ADD COLUMN art_candidates TEXT;   -- JSON [{"source": .., "url": ..}]
ALTER TABLE titles ADD COLUMN art_source     TEXT;   -- NULL, 'jellyfin' or 'poster'
