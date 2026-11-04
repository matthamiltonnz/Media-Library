-- "iTunes" and "other" were placeholder shops: an album bought digitally is
-- Apple Music, a film is the Apple TV app, and a real shop gets its own row
-- (the pickers create one from a typed name). Move what is left of the two
-- keys, then drop the rows - only when nothing points at them any more.

UPDATE digital_sources SET provider = 'applemusic'
 WHERE provider = 'itunes'
   AND item_id IN (SELECT i.id FROM items i JOIN titles t ON t.id = i.title_id
                    WHERE t.kind = 'music');

UPDATE digital_sources SET provider = 'appletv'
 WHERE provider = 'itunes';

DELETE FROM store_targets
 WHERE store_id IN (SELECT id FROM stores WHERE key IN ('itunes', 'other'));

DELETE FROM stores
 WHERE key IN ('itunes', 'other')
   AND NOT EXISTS (SELECT 1 FROM digital_sources d WHERE d.provider = stores.key)
   AND NOT EXISTS (SELECT 1 FROM items i WHERE i.store_id = stores.id);
