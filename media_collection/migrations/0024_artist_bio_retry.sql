-- 0024_artist_bio_retry.sql
-- The first biography fill looked each act up on Wikipedia by *name* only, so an
-- act whose name is also an ordinary word came back empty and was stamped with
-- bio_at - which is what stops the background job asking about it again. "Air"
-- resolved to the gas, so the French band has no biography.
--
-- metadata.artist_bio() now starts from the MusicBrainz id (id -> Wikidata ->
-- the English article), so the acts that failed under the old rule are released
-- to be asked once more. Acts that already have an article are left alone.
UPDATE artists SET bio_at = NULL WHERE wiki_url IS NULL;
