-- 0002_catalog.sql
-- Separate "what you own" (title + copy) from "how you get to it" (digital source,
-- playback target), and add the import staging tables used to load a Blu-ray.com
-- style CSV export for review before it touches the catalogue.

PRAGMA foreign_keys = ON;

-- ------------------------------------------------------- title identifiers --
ALTER TABLE titles ADD COLUMN imdb_id TEXT;
CREATE UNIQUE INDEX ux_titles_imdb ON titles (imdb_id) WHERE imdb_id IS NOT NULL;

-- ------------------------------------------------------- copy identifiers ---
ALTER TABLE items ADD COLUMN region        TEXT;   -- 'A', 'B', 'C', 'free'
ALTER TABLE items ADD COLUMN bluray_com_id INTEGER;

-- ------------------------------------------------------- digital sources ----
-- A copy can be reachable through several libraries (a rip in Jellyfin *and* a
-- Kodi library); provider + external_id is what the router needs to play it.

CREATE TABLE digital_sources (
    id          INTEGER PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES items (id) ON DELETE CASCADE,
    provider    TEXT    NOT NULL
                        CHECK (provider IN ('itunes', 'appletv', 'jellyfin', 'kodi', 'other')),
    library_id  TEXT,                              -- Jellyfin library / Kodi source
    external_id TEXT,                              -- Jellyfin item id / Kodi file or db id
    notes       TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (item_id, provider, external_id)
);

CREATE INDEX ix_digital_sources_item ON digital_sources (item_id);

-- ------------------------------------------------------ playback targets ----
-- Devices the "play this" action can drive. ha_entity_id is a media_player.*
-- entity in Home Assistant; input_source is the receiver/HDMI input to switch to.

CREATE TABLE playback_targets (
    id            INTEGER PRIMARY KEY,
    name          TEXT    NOT NULL UNIQUE,          -- 'Lounge Blu-ray player'
    kind          TEXT    NOT NULL
                          CHECK (kind IN ('bluray_player', 'apple_tv', 'jellyfin', 'kodi')),
    ha_entity_id  TEXT,                             -- media_player.lounge_bd
    input_source  TEXT,                             -- 'BD/DVD', 'HDMI 2'
    app_name      TEXT,                             -- 'Jellyfin', 'Apple TV'
    notes         TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ------------------------------------------------------------- imports ------

CREATE TABLE imports (
    id           INTEGER PRIMARY KEY,
    filename     TEXT,
    raw_csv      TEXT    NOT NULL,
    mapping_json TEXT,                              -- {field: csv header}
    row_count    INTEGER NOT NULL DEFAULT 0,
    created_by   INTEGER REFERENCES users (id) ON DELETE SET NULL,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    committed_at TEXT
);

CREATE TABLE import_rows (
    id            INTEGER PRIMARY KEY,
    import_id     INTEGER NOT NULL REFERENCES imports (id) ON DELETE CASCADE,
    line_no       INTEGER NOT NULL,
    title         TEXT,
    release_year  INTEGER,
    kind          TEXT,                             -- movie / tv / other
    upc_ean       TEXT,
    format        TEXT,
    region        TEXT,
    bluray_com_id INTEGER,
    edition       TEXT,
    location      TEXT,
    condition     TEXT,
    provider      TEXT,                             -- digital provider, if listed
    status        TEXT    NOT NULL CHECK (status IN ('ok', 'duplicate', 'error')),
    message       TEXT,
    raw_json      TEXT
);

CREATE INDEX ix_import_rows_import ON import_rows (import_id, status);

-- ------------------------------------------------ views include new columns --

DROP VIEW v_shared_library;
CREATE VIEW v_shared_library AS
SELECT i.id            AS item_id,
       t.id            AS title_id,
       t.name          AS title,
       t.release_year,
       t.kind,
       t.imdb_id,
       i.format,
       i.edition,
       i.upc_ean,
       i.region,
       i.bluray_com_id,
       u.id            AS owner_id,
       u.display_name  AS owner,
       i.location,
       i.condition,
       i.acquired_on,
       i.notes
FROM   items i
JOIN   titles t ON t.id = i.title_id
JOIN   users  u ON u.id = i.owner_id
WHERE  i.visibility = 'shared'
  AND  u.is_active = 1;

CREATE VIEW v_digital_sources AS
SELECT d.id,
       d.provider,
       d.library_id,
       d.external_id,
       t.name AS title,
       t.release_year,
       u.display_name AS owner
FROM   digital_sources d
JOIN   items  i ON i.id = d.item_id
JOIN   titles t ON t.id = i.title_id
JOIN   users  u ON u.id = i.owner_id;
