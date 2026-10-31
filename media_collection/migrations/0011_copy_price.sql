-- 0011_copy_price.sql
-- What a copy cost, so a collection can be valued: the purchase price next to the
-- date it was bought (items.acquired_on already holds that) and the store it came
-- from (digital_sources.provider, e.g. itunes).

ALTER TABLE items ADD COLUMN price_paid TEXT;
