-- A picture of an act can be chosen, and remembered.
--
-- artists.image_url has always held one url; the act page now offers the
-- pictures a lookup can find (the Wikipedia article's lead image, its Wikidata
-- P18, the lead images of the pages a name search turns up) plus an upload, so
-- the ones already offered are kept here as a JSON list {"source", "url",
-- "label"} - the same shape as titles.art_candidates, capped in Python.
--
-- The second statement is the repair: `fill_bio` used to write image_url
-- straight from the article summary, so "Fetch details" on an act whose article
-- has no photograph wiped a portrait that had come from Wikidata.

ALTER TABLE artists ADD COLUMN image_candidates TEXT;

UPDATE artists SET image_at = NULL
 WHERE image_url IS NULL OR TRIM(image_url) = '';
