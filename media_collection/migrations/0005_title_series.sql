-- When Wikidata was last asked which film series a title belongs to.
--
-- Same idea as artists.bio_at: most films are not part of any series, and
-- without the stamp every refresh would ask again about every one of them.
ALTER TABLE titles ADD COLUMN series_at TEXT;
