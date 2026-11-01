-- 0015_upc_per_owner.sql
-- A barcode used to be unique across the whole library, so a second household
-- member could not hold the same disc: imports dropped every row whose barcode
-- was already owned by someone else. Two people can own the same edition, so the
-- uniqueness is per owner now (and still one barcode per copy each).

DROP INDEX IF EXISTS ux_items_upc;
CREATE UNIQUE INDEX IF NOT EXISTS ux_items_upc_owner
    ON items (upc_ean, owner_id) WHERE upc_ean IS NOT NULL;
