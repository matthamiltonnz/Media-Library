-- 0026_stores.sql
-- A store was a hardcoded pair of keys in server.py with labels in
-- ui_title.py, and "iTunes" and "Apple TV" were folded into one key ('itunes'
-- rendered as "Apple TV"). Stores are data now: one row per shop, optionally
-- pointing at the device that can open it - playback_targets has been sitting
-- in the schema unused since 0002, with exactly the columns that needs
-- (name, ha_entity_id, input_source, app_name).
--
-- digital_sources.provider still holds the key, so an old row keeps working
-- whether or not a store row exists for it.

CREATE TABLE IF NOT EXISTS stores (
    id          INTEGER PRIMARY KEY,
    key         TEXT    NOT NULL UNIQUE,     -- what digital_sources.provider holds
    label       TEXT    NOT NULL,            -- 'iTunes', 'Apple TV'
    kind        TEXT    NOT NULL DEFAULT 'store'
                        CHECK (kind IN ('store', 'system')),
    target_id   INTEGER REFERENCES playback_targets(id) ON DELETE SET NULL,
    app_name    TEXT,                        -- app to open on that device
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- 'store' rows are shops the pickers offer; 'system' rows are the keys the app
-- itself writes (a Jellyfin route, a ripped file) and are never chosen by hand.
INSERT OR IGNORE INTO stores (key, label, kind) VALUES
    ('itunes',   'iTunes',      'store'),
    ('appletv',  'Apple TV',    'store'),
    ('other',    'other store', 'store'),
    ('jellyfin', 'Jellyfin',    'system'),
    ('rip',      'ripped file', 'system');

-- Every 'itunes' row on the shelf today is a film bought in the Apple TV app -
-- 167 of them, none of them music - so they move to 'appletv' and 'itunes' is
-- free to mean the music store.
UPDATE digital_sources SET provider = 'appletv'
 WHERE provider = 'itunes'
   AND item_id IN (SELECT i.id FROM items i
                     JOIN titles t ON t.id = i.title_id
                    WHERE t.kind <> 'music');
