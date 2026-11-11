-- A portrait for an act, looked up once.
--
-- artists.image_url has been in the schema from the start and the biography
-- lookup has been filling it with the Wikipedia article's lead image, but
-- nothing on the page used it: the artist wall and the catalogue's search cards
-- showed the sleeve of whichever album came first, so a shelf of acts read as a
-- shelf of records.
--
-- image_at is fill_images()'s stamp, exactly as bio_at is fill_bio()'s. The
-- lookup only asks about acts with an empty image_url and no stamp, so an act
-- nobody has a photograph of is recorded as asked instead of costing two or
-- three requests on every refresh.

ALTER TABLE artists ADD COLUMN image_at TEXT;

-- an act that already carries a portrait has had its answer
UPDATE artists SET image_at = COALESCE(bio_at, datetime('now'))
 WHERE image_url IS NOT NULL AND TRIM(image_url) <> '';
