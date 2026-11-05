-- 0001_schema.sql
--
-- The schema, in one file. This is the baseline: a brand-new database is built
-- by this file alone, and a database that already has tables is recorded as
-- having it (see migrate.sh) instead of being rebuilt under itself.
--
-- Seeded here: the shops and platforms the app knows by name, and one row per
-- physical format so a format can carry players. Nothing else - no media root,
-- no owners, no titles. Those are yours to add.
--
-- Was 33 files, 0001_init to 0033_owner_first_names; the history is gone with
-- 0.23.134 because nobody else had a database to migrate.

CREATE TABLE users (
    id            INTEGER PRIMARY KEY,
    username      TEXT    NOT NULL,              -- login handle, case-insensitive
    display_name  TEXT    NOT NULL,              -- "Mum", "Matt", ...
    password_hash TEXT,                          -- NULL = profile-only (no login)
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX ux_users_username ON users (lower(username));
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
CREATE TABLE media_roots (
    id         INTEGER PRIMARY KEY,
    label      TEXT    NOT NULL UNIQUE,
    path       TEXT    NOT NULL UNIQUE,
    medium     TEXT    NOT NULL DEFAULT 'video'
                       CHECK (medium IN ('video', 'music', 'other')),
    owner_id   INTEGER REFERENCES users (id) ON DELETE SET NULL,
    notes      TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE "titles" (
    id                  INTEGER PRIMARY KEY,
    name                TEXT    NOT NULL,
    sort_name           TEXT,
    release_year        INTEGER CHECK (release_year BETWEEN 1500 AND 2100),
    kind                TEXT    NOT NULL DEFAULT 'movie'
                                CHECK (kind IN ('movie', 'tv', 'music', 'other')),
    tmdb_id             INTEGER,
    imdb_id             TEXT,
    notes               TEXT,
    created_by          INTEGER REFERENCES users (id) ON DELETE SET NULL,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    overview            TEXT,
    poster_url          TEXT,
    runtime_minutes     INTEGER,
    genres              TEXT,
    rating              TEXT,
    metadata_source     TEXT,
    metadata_updated_at TEXT
, musicbrainz_id TEXT, artist_id INTEGER REFERENCES artists (id) ON DELETE SET NULL, art_candidates TEXT, art_source     TEXT);
CREATE UNIQUE INDEX ux_titles_tmdb ON titles (tmdb_id) WHERE tmdb_id IS NOT NULL;
CREATE INDEX ix_titles_name ON titles (lower(name));
CREATE INDEX ix_titles_imdb ON titles (imdb_id);
CREATE INDEX ix_titles_mbid ON titles (musicbrainz_id);
CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE jellyfin_links (
    item_id    INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    jf_id      TEXT    NOT NULL,
    jf_kind    TEXT    NOT NULL
                       CHECK (jf_kind IN ('movie', 'album', 'season', 'series', 'episode')),
    jf_name    TEXT,
    jf_path    TEXT,
    matched_by TEXT    NOT NULL DEFAULT 'path' CHECK (matched_by IN ('path', 'name')),
    synced_at  TEXT    NOT NULL DEFAULT (datetime('now'))
, server_id INTEGER
    REFERENCES jellyfin_servers(id));
CREATE TABLE artists (
    id             INTEGER PRIMARY KEY,
    name           TEXT    NOT NULL,
    sort_name      TEXT,
    musicbrainz_id TEXT,
    country        TEXT,
    formed_year    INTEGER,
    overview       TEXT,
    image_url      TEXT,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
, kind TEXT, wiki_url  TEXT, link_urls TEXT, bio_at    TEXT);
CREATE UNIQUE INDEX ux_artists_name ON artists (name);
CREATE INDEX ix_titles_artist ON titles (artist_id);
CREATE TABLE "items" (
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
    last_watched_on TEXT, price_paid TEXT, rip_path    TEXT, rip_root_id INTEGER REFERENCES media_roots (id), store_id INTEGER REFERENCES stores (id),
    CHECK (upc_ean IS NULL
           OR (length(upc_ean) IN (12, 13) AND upc_ean NOT GLOB '*[^0-9]*'))
);
CREATE INDEX ix_items_owner        ON items (owner_id);
CREATE INDEX ix_items_title        ON items (title_id);
CREATE INDEX ix_items_last_watched ON items (last_watched_on);
CREATE VIEW v_owner_totals AS
SELECT u.id            AS owner_id,
       u.display_name  AS owner,
       COUNT(i.id)     AS item_count,
       SUM(i.visibility = 'shared') AS shared_count,
       SUM(i.visibility = 'private') AS private_count
FROM   users u
LEFT   JOIN items i ON i.owner_id = u.id
WHERE  u.is_active = 1
GROUP  BY u.id, u.display_name
/* v_owner_totals(owner_id,owner,item_count,shared_count,private_count) */;
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
  AND  u.is_active = 1
/* v_shared_library(item_id,title_id,title,release_year,kind,imdb_id,format,edition,upc_ean,region,bluray_com_id,owner_id,owner,location,condition,acquired_on,notes) */;
CREATE TRIGGER trg_users_block_delete_with_items
BEFORE DELETE ON users
WHEN EXISTS (SELECT 1 FROM items WHERE owner_id = OLD.id)
BEGIN
    SELECT RAISE(ABORT, 'user still owns items; deactivate instead of deleting');
END;
CREATE UNIQUE INDEX ux_items_upc_owner
    ON items (upc_ean, owner_id) WHERE upc_ean IS NOT NULL;
CREATE INDEX ix_items_rip_path ON items (rip_root_id, rip_path);
CREATE TABLE jellyfin_servers (
    id          INTEGER PRIMARY KEY,
    label       TEXT NOT NULL,
    url         TEXT NOT NULL,
    api_key     TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX ix_jellyfin_links_server
    ON jellyfin_links(server_id, jf_id);
CREATE TRIGGER titles_sort_name_on_insert
AFTER INSERT ON titles
FOR EACH ROW
WHEN NEW.sort_name IS NULL OR NEW.sort_name = ''
BEGIN
  UPDATE titles SET sort_name = CASE
      WHEN lower(substr(NEW.name, 1, 4)) = 'the ' THEN substr(NEW.name, 5) || ', The'
      WHEN lower(substr(NEW.name, 1, 2)) = 'a '   THEN substr(NEW.name, 3) || ', A'
      WHEN lower(substr(NEW.name, 1, 3)) = 'an '  THEN substr(NEW.name, 4) || ', An'
      ELSE NEW.name
    END
  WHERE id = NEW.id;
END;
CREATE TRIGGER titles_sort_name_on_rename
AFTER UPDATE OF name ON titles
FOR EACH ROW
WHEN NEW.name <> OLD.name
BEGIN
  UPDATE titles SET sort_name = CASE
      WHEN lower(substr(NEW.name, 1, 4)) = 'the ' THEN substr(NEW.name, 5) || ', The'
      WHEN lower(substr(NEW.name, 1, 2)) = 'a '   THEN substr(NEW.name, 3) || ', A'
      WHEN lower(substr(NEW.name, 1, 3)) = 'an '  THEN substr(NEW.name, 4) || ', An'
      ELSE NEW.name
    END
  WHERE id = NEW.id;
END;
CREATE TABLE "digital_sources" (
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
CREATE INDEX ix_digital_sources_item
    ON digital_sources (item_id);
CREATE INDEX ix_digital_sources_server
    ON digital_sources (server_id, provider);
CREATE VIEW v_digital_sources AS
SELECT d.id, d.provider, d.library_id, d.external_id, d.path,
       r.label AS root, r.path AS root_path,
       t.name AS title, t.release_year, u.display_name AS owner
FROM   digital_sources d
JOIN   items  i ON i.id = d.item_id
JOIN   titles t ON t.id = i.title_id
JOIN   users  u ON u.id = i.owner_id
LEFT   JOIN media_roots r ON r.id = d.root_id
/* v_digital_sources(id,provider,library_id,external_id,path,root,root_path,title,release_year,owner) */;
CREATE TABLE "playback_targets" (
    id            INTEGER PRIMARY KEY,
    name          TEXT    NOT NULL UNIQUE,
    kind          TEXT    NOT NULL,
    ha_entity_id  TEXT,
    input_source  TEXT,
    app_name      TEXT,
    notes         TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE "stores" (
    id          INTEGER PRIMARY KEY,
    key         TEXT    NOT NULL UNIQUE,
    label       TEXT    NOT NULL,
    kind        TEXT    NOT NULL DEFAULT 'digital',  -- digital | retail | system
    app_name    TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE store_targets (
    store_id   INTEGER NOT NULL REFERENCES stores (id) ON DELETE CASCADE,
    target_id  INTEGER NOT NULL REFERENCES playback_targets (id) ON DELETE CASCADE, app TEXT,
    PRIMARY KEY (store_id, target_id)
);


-- The shops and platforms a digital copy comes from, and the two keys the app
-- writes itself. A shop the catalogue has not been told about is created the
-- first time someone types it, so this list is a starting point, not a limit.
INSERT INTO stores (key, label, kind) VALUES
    ('appletv',      'Apple TV',      'digital'),
    ('jellyfin',     'Jellyfin',      'system'),
    ('rip',          'ripped file',   'system'),
    ('applemusic',   'Apple Music',   'digital'),
    ('googletv',     'Google TV',     'digital'),
    ('youtubemusic', 'YouTube Music', 'digital');

-- One row per physical format: `key` is an `items.format`, and the row exists so
-- a format can carry players (Admin -> Players by format) even though no copy
-- ever names it as a store.
INSERT INTO stores (key, label, kind) VALUES
    ('bluray_3d', '3D Blu-ray', 'format'),
    ('bluray',    'Blu-ray',    'format'),
    ('uhd_4k',    '4K Blu-ray', 'format'),
    ('dvd',       'DVD',        'format'),
    ('cd',        'CD',         'format'),
    ('vinyl',     'Vinyl',      'format'),
    ('sacd',      'SACD',       'format'),
    ('cassette',  'Cassette',   'format'),
    ('vhs',       'VHS',        'format'),
    ('other',     'Other',      'format');
