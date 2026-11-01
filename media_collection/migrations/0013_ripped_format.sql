-- 0013_ripped_format.sql
-- A copy that is a file on the NAS was filed as "digital", which reads as
-- "bought from a store". It gets its own format so the shelf tells a rip from a
-- purchase. SQLite cannot widen a CHECK in place, so items is rebuilt the way
-- 0005 did it, then every existing rip copy is reclassified.
--
-- The views and the trigger over items are dropped first and recreated at the
-- end: SQLite refuses the DROP/RENAME while a view or trigger still points at
-- the old name, and a half-finished rebuild is what repair.py cleans up next
-- boot (taking the copied rows with it).

PRAGMA foreign_keys = OFF;   -- keep the digital_sources rows through the rebuild

DROP VIEW IF EXISTS v_digital_sources;
DROP VIEW IF EXISTS v_owner_totals;
DROP VIEW IF EXISTS v_shared_library;
DROP TRIGGER IF EXISTS trg_users_block_delete_with_items;
DROP TABLE IF EXISTS items_new;   -- leftovers from an interrupted attempt

CREATE TABLE items_new (
    id            INTEGER PRIMARY KEY,
    title_id      INTEGER NOT NULL REFERENCES titles (id) ON DELETE RESTRICT,
    owner_id      INTEGER NOT NULL REFERENCES users (id)  ON DELETE RESTRICT,
    format        TEXT    NOT NULL
                          CHECK (format IN ('bluray', 'bluray_3d', 'uhd_4k', 'dvd',
                                            'vhs', 'digital', 'ripped', 'ripped', 'cd', 'vinyl', 'sacd',
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

CREATE VIEW v_digital_sources AS
SELECT d.id, d.provider, d.library_id, d.external_id, d.path,
       r.label AS root, r.path AS root_path,
       t.name AS title, t.release_year, u.display_name AS owner
FROM   digital_sources d
JOIN   items  i ON i.id = d.item_id
JOIN   titles t ON t.id = i.title_id
JOIN   users  u ON u.id = i.owner_id
LEFT   JOIN media_roots r ON r.id = d.root_id;
CREATE VIEW v_owner_totals AS
SELECT u.id            AS owner_id,
       u.display_name  AS owner,
       COUNT(i.id)     AS item_count,
       SUM(i.visibility = 'shared') AS shared_count,
       SUM(i.visibility = 'private') AS private_count
FROM   users u
LEFT   JOIN items i ON i.owner_id = u.id
WHERE  u.is_active = 1
GROUP  BY u.id, u.display_name;
CREATE VIEW v_shared_library AS
SELECT i.id           AS item_id,
       t.id           AS title_id,
       t.name         AS title,
       t.release_year,
       t.kind,
       t.imdb_id,
       i.format,
       i.edition,
       i.upc_ean,
       i.region,
       i.bluray_com_id,
       u.id           AS owner_id,
       u.display_name AS owner,
       i.location,
       i.condition,
       i.acquired_on,
       i.notes
FROM   items i
JOIN   titles t ON t.id = i.title_id
JOIN   users  u ON u.id = i.owner_id
WHERE  i.visibility = 'shared'
  AND  u.is_active = 1;
CREATE TRIGGER trg_users_block_delete_with_items
BEFORE DELETE ON users
WHEN EXISTS (SELECT 1 FROM items WHERE owner_id = OLD.id)
BEGIN
    SELECT RAISE(ABORT, 'user still owns items; deactivate instead of deleting');
END;
PRAGMA foreign_keys = ON;
