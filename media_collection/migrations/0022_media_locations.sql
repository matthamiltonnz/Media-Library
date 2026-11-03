-- 0022_media_locations.sql
-- Where the household keeps things.
--
-- Every copy had an empty location before this, so these are the catalogue's
-- first locations:
--
--   vinyl, whoever owns it          -> Lounge kallax
--   Matt's films, TV and box sets   -> Lounge shelf
--   Matt's CDs, SACDs, cassettes    -> Bedroom storage
--   Mitchell's films, TV and discs  -> Mitchell's bedroom
--
-- Only physical copies are touched. `digital` (an iTunes purchase) and
-- `ripped` (a route to Jellyfin) are not on a shelf, so they keep a NULL
-- location - as do the copies of anyone else in the household.

UPDATE items
   SET location = 'Lounge kallax'
 WHERE format = 'vinyl';

UPDATE items
   SET location = 'Lounge shelf'
 WHERE owner_id = (SELECT id FROM users WHERE display_name = 'Matt Hamilton')
   AND format IN ('bluray', 'bluray_3d', 'uhd_4k', 'dvd', 'vhs', 'other');

UPDATE items
   SET location = 'Bedroom storage'
 WHERE owner_id = (SELECT id FROM users WHERE display_name = 'Matt Hamilton')
   AND format IN ('cd', 'sacd', 'cassette');

UPDATE items
   SET location = 'Mitchell''s bedroom'
 WHERE owner_id = (SELECT id FROM users WHERE display_name = 'Mitchell')
   AND format IN ('bluray', 'bluray_3d', 'uhd_4k', 'dvd', 'vhs', 'other',
                  'cd', 'sacd', 'cassette');
