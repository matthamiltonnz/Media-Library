-- Music titles were stored as "Artist — Album" in `name`, with the artist also
-- held in `titles.artist_id`. Now that the artist is its own field, the name
-- should be the album only. artists.SEPARATOR is " — ".
UPDATE titles
   SET name = trim(substr(name,
                          length((SELECT a.name FROM artists a
                                   WHERE a.id = titles.artist_id)) + 4))
 WHERE kind = 'music'
   AND artist_id IS NOT NULL
   AND name LIKE (SELECT a.name || ' — %' FROM artists a
                   WHERE a.id = titles.artist_id);
