-- 0004_ripped_sources.sql
-- A copy can also exist as a ripped file on the NAS (the HA media share), so add a
-- 'rip' provider and a path. SQLite cannot alter a CHECK constraint, so rebuild.
--
-- Written to survive an interrupted run: the scratch table is dropped first, the
-- old table is recreated if a previous attempt removed it, and the whole rebuild
-- happens inside one transaction.

PRAGMA foreign_keys = OFF;
BEGIN IMMEDIATE;

-- an invalid view left by an interrupted run makes DROP TABLE fail, so clear it first
DROP VIEW IF EXISTS v_digital_sources;

DROP TABLE IF EXISTS digital_sources_new;

-- recovery: an earlier interrupted run may have dropped the original table
CREATE TABLE IF NOT EXISTS digital_sources (
    id          INTEGER PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES items (id) ON DELETE CASCADE,
    provider    TEXT    NOT NULL
                        CHECK (provider IN ('itunes', 'appletv', 'jellyfin', 'kodi', 'other')),
    library_id  TEXT,
    external_id TEXT,
    notes       TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (item_id, provider, external_id)
);

CREATE TABLE digital_sources_new (
    id          INTEGER PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES items (id) ON DELETE CASCADE,
    provider    TEXT    NOT NULL
                        CHECK (provider IN ('rip', 'jellyfin', 'kodi', 'itunes',
                                            'appletv', 'other')),
    library_id  TEXT,
    external_id TEXT,
    path        TEXT,                            -- relative to the media share
    notes       TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (item_id, provider, external_id)
);

INSERT INTO digital_sources_new
       (id, item_id, provider, library_id, external_id, notes, created_at)
SELECT  id, item_id, provider, library_id, external_id, notes, created_at
  FROM  digital_sources;

DROP TABLE digital_sources;
ALTER TABLE digital_sources_new RENAME TO digital_sources;
CREATE INDEX ix_digital_sources_item ON digital_sources (item_id);

DROP VIEW IF EXISTS v_digital_sources;
CREATE VIEW v_digital_sources AS
SELECT d.id,
       d.provider,
       d.library_id,
       d.external_id,
       d.path,
       t.name AS title,
       t.release_year,
       u.display_name AS owner
FROM   digital_sources d
JOIN   items  i ON i.id = d.item_id
JOIN   titles t ON t.id = i.title_id
JOIN   users  u ON u.id = i.owner_id;


COMMIT;
PRAGMA foreign_keys = ON;
