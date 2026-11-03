-- 0028_retail_and_players.sql
-- Three things the shop record could not say.
--
-- 1. A store is either a *digital* shop (Apple TV, iTunes, Bandcamp) or a
--    *retail* one (a disc bought in a shop), and a physical copy had nowhere to
--    record the latter: 'store' becomes 'digital', and items.store_id holds the
--    retail one. The copy form keeps a single Store box - the format decides
--    which of the two it means.
-- 2. A store is not one device. The Apple TV app runs on the Apple TV, on an
--    Android/Google TV and elsewhere, so stores.target_id becomes the
--    store_targets join table: any number of players per store.
-- 3. A player is not always a media_player. Turning a disc player on may mean
--    setting an input_select that fires an automation, or running a script, so
--    playback_targets loses its CHECK on kind and gains those domains.

-- The links have to survive the rebuild of both tables, so park them first.
CREATE TEMP TABLE pending_links AS
SELECT id AS store_id, target_id FROM stores WHERE target_id IS NOT NULL;

-- 1. playback_targets: same columns, no CHECK on kind.
CREATE TABLE playback_targets_new (
    id            INTEGER PRIMARY KEY,
    name          TEXT    NOT NULL UNIQUE,
    kind          TEXT    NOT NULL,
    ha_entity_id  TEXT,
    input_source  TEXT,
    app_name      TEXT,
    notes         TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO playback_targets_new (id, name, kind, ha_entity_id, input_source,
                                  app_name, notes, created_at)
SELECT id, name, kind, ha_entity_id, input_source, app_name, notes, created_at
  FROM playback_targets;
DROP TABLE playback_targets;
ALTER TABLE playback_targets_new RENAME TO playback_targets;

-- 2. stores: 'store' -> 'digital', no CHECK, no single target.
CREATE TABLE stores_new (
    id          INTEGER PRIMARY KEY,
    key         TEXT    NOT NULL UNIQUE,
    label       TEXT    NOT NULL,
    kind        TEXT    NOT NULL DEFAULT 'digital',  -- digital | retail | system
    app_name    TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO stores_new (id, key, label, kind, app_name, is_active, created_at)
SELECT id, key, label,
       CASE kind WHEN 'store' THEN 'digital' ELSE kind END,
       app_name, is_active, created_at
  FROM stores;
DROP TABLE stores;
ALTER TABLE stores_new RENAME TO stores;

-- 3. A store's players.
CREATE TABLE IF NOT EXISTS store_targets (
    store_id   INTEGER NOT NULL REFERENCES stores (id) ON DELETE CASCADE,
    target_id  INTEGER NOT NULL REFERENCES playback_targets (id) ON DELETE CASCADE,
    PRIMARY KEY (store_id, target_id)
);

INSERT OR IGNORE INTO store_targets (store_id, target_id)
SELECT store_id, target_id FROM pending_links;

DROP TABLE pending_links;

-- 4. The retail shop on a physical copy.
ALTER TABLE items ADD COLUMN store_id INTEGER REFERENCES stores (id);
