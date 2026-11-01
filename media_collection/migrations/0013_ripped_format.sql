-- 0013_ripped_format.sql
-- A copy that is a file on the NAS was filed as "digital", which reads as
-- "bought from a store". It gets its own format so the shelf tells a rip from a
-- purchase. SQLite cannot widen a CHECK in place, so items is rebuilt the way
-- 0005 did it, then every existing rip copy is reclassified.

PRAGMA foreign_keys = OFF;   -- keep the digital_sources rows through the rebuild

CREATE TABLE items_new (
    id            INTEGER PRIMARY KEY,
    title_id      INTEGER NOT NULL REFERENCES titles (id) ON DELETE RESTRICT,
    owner_id      INTEGER NOT NULL REFERENCES users (id)  ON DELETE RESTRICT,
    format        TEXT    NOT NULL
                          CHECK (format IN ('bluray', 'bluray_3d', 'uhd_4k', 'dvd',
                                            'vhs', 'digital', 'ripped', 'cd', 'vinyl', 'sacd',
                                            'cassette', 'other')),
    edition       TEXT,
    upc_ean       TEXT,
    location      TEXT,
    condition     TEXT,
    acquired_on   TEXT,
    visibility    TEXT    NOT NULL DEFAULT 'shared'
                          CHECK (visibility IN ('shared', 'private')),
    notes         TEXT,
    added_by      INTEGER REFERENCES users (id) ON DELETE SET NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    region        TEXT,
    bluray_com_id INTEGER,
    last_watched_on TEXT, price_paid TEXT,
    CHECK (upc_ean IS NULL
           OR (length(upc_ean) IN (12, 13) AND upc_ean NOT GLOB '*[^0-9]*'))
);

INSERT INTO items_new (id, title_id, owner_id, format, edition, upc_ean, location, condition, acquired_on, visibility, notes, added_by, created_at, updated_at, region, bluray_com_id, last_watched_on, price_paid)
SELECT id, title_id, owner_id, format, edition, upc_ean, location, condition, acquired_on, visibility, notes, added_by, created_at, updated_at, region, bluray_com_id, last_watched_on, price_paid FROM items;

DROP TABLE items;
ALTER TABLE items_new RENAME TO items;
CREATE UNIQUE INDEX ux_items_upc          ON items (upc_ean);
CREATE INDEX        ix_items_owner        ON items (owner_id);
CREATE INDEX        ix_items_title        ON items (title_id);
CREATE INDEX        ix_items_last_watched ON items (last_watched_on);

-- a copy with a rip source behind it is a ripped file, whatever it was called
UPDATE items SET format = 'ripped'
 WHERE format = 'digital'
   AND EXISTS (SELECT 1 FROM digital_sources r
                WHERE r.item_id = items.id AND r.provider = 'rip');

PRAGMA foreign_keys = ON;
