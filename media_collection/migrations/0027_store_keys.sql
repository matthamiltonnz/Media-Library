-- 0027_store_keys.sql
-- `digital_sources.provider` carried a CHECK listing the six providers that
-- existed when the table was made ('rip', 'jellyfin', 'kodi', 'itunes',
-- 'appletv', 'other'). Stores are data now (0026) and a shop can be typed into
-- the picker, so the CHECK has to go: saving a copy from, say, Bandcamp failed
-- with "CHECK constraint failed: provider IN (...)".
--
-- SQLite cannot drop a constraint, so the table is rebuilt: same columns, same
-- foreign keys, same UNIQUE, without the CHECK. Nothing references
-- digital_sources, and the view and both indexes are put back below.

DROP VIEW IF EXISTS v_digital_sources;

CREATE TABLE digital_sources_new (
    id          INTEGER PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES items (id) ON DELETE CASCADE,
    provider    TEXT    NOT NULL,
    library_id  TEXT,
    external_id TEXT,
    path        TEXT,
    notes       TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    root_id     INTEGER REFERENCES media_roots (id),
    server_id   INTEGER REFERENCES jellyfin_servers(id),
    media_source_id   TEXT,
    media_source_name TEXT,
    UNIQUE (item_id, provider, external_id)
);

INSERT INTO digital_sources_new (id, item_id, provider, library_id, external_id,
                                 path, notes, created_at, root_id, server_id,
                                 media_source_id, media_source_name)
SELECT id, item_id, provider, library_id, external_id, path, notes,
       created_at, root_id, server_id, media_source_id, media_source_name
  FROM digital_sources;

DROP TABLE digital_sources;
ALTER TABLE digital_sources_new RENAME TO digital_sources;

CREATE INDEX IF NOT EXISTS ix_digital_sources_item
    ON digital_sources (item_id);
CREATE INDEX IF NOT EXISTS ix_digital_sources_server
    ON digital_sources (server_id, provider);

CREATE VIEW v_digital_sources AS
SELECT d.id, d.provider, d.library_id, d.external_id, d.path,
       r.label AS root, r.path AS root_path,
       t.name AS title, t.release_year, u.display_name AS owner
FROM   digital_sources d
JOIN   items  i ON i.id = d.item_id
JOIN   titles t ON t.id = i.title_id
JOIN   users  u ON u.id = i.owner_id
LEFT   JOIN media_roots r ON r.id = d.root_id;
