-- 0005_media_roots_music.sql
-- 1. Media roots (NAS folders: Matt's movies, iTunes, TV, Mitchell's movies).
-- 2. Ripped sources point at a root + relative path.
-- 3. Music: CD / Vinyl / SACD formats and a 'music' title kind.
-- SQLite cannot widen a CHECK constraint, so items and titles are rebuilt.

PRAGMA foreign_keys = OFF;
BEGIN IMMEDIATE;

-- views and triggers reference the tables being rebuilt; a dangling one makes
-- DROP TABLE fail, so they are dropped and recreated around the rebuild
DROP VIEW IF EXISTS v_shared_library;
DROP VIEW IF EXISTS v_digital_sources;
DROP VIEW IF EXISTS v_owner_totals;
DROP TRIGGER IF EXISTS trg_users_block_delete_with_items;

DROP TABLE IF EXISTS titles_new;
DROP TABLE IF EXISTS items_new;

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

INSERT INTO media_roots (label, path, medium, owner_id) VALUES
    ('Matt — Movies',       '/media/My Media/QNAP/Movies',             'video',
     (SELECT id FROM users WHERE lower(username) = 'matt')),
    ('iTunes Movies',       '/media/My Media/QNAP/iTunes Movies',      'video', NULL),
    ('TV Shows',            '/media/My Media/QNAP/TV',                 'video', NULL),
    ('Mitchell — Movies',   '/media/My Media/QNAP/Mitchell''s Movies', 'video',
     (SELECT id FROM users WHERE lower(display_name) LIKE 'mitchell%' LIMIT 1));

ALTER TABLE digital_sources ADD COLUMN root_id INTEGER REFERENCES media_roots (id);

-- ------------------------------------------------------------------ titles --
CREATE TABLE titles_new (
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
);

INSERT INTO titles_new (id, name, sort_name, release_year, kind, tmdb_id, imdb_id,
                        notes, created_by, created_at, overview, poster_url,
                        runtime_minutes, genres, rating, metadata_source,
                        metadata_updated_at)
SELECT id, name, sort_name, release_year, kind, tmdb_id, imdb_id, notes, created_by,
       created_at, overview, poster_url, runtime_minutes, genres, rating,
       metadata_source, metadata_updated_at
FROM   titles;

DROP TABLE titles;
ALTER TABLE titles_new RENAME TO titles;
CREATE UNIQUE INDEX ux_titles_tmdb ON titles (tmdb_id) WHERE tmdb_id IS NOT NULL;
CREATE INDEX        ix_titles_name ON titles (lower(name));
CREATE INDEX        ix_titles_imdb ON titles (imdb_id);


-- ------------------------------------------------------------------- items --
CREATE TABLE items_new (
    id            INTEGER PRIMARY KEY,
    title_id      INTEGER NOT NULL REFERENCES titles (id) ON DELETE RESTRICT,
    owner_id      INTEGER NOT NULL REFERENCES users (id)  ON DELETE RESTRICT,
    format        TEXT    NOT NULL
                          CHECK (format IN ('bluray', 'bluray_3d', 'uhd_4k', 'dvd',
                                            'vhs', 'digital', 'cd', 'vinyl', 'sacd',
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
    last_watched_on TEXT,
    CHECK (upc_ean IS NULL
           OR (length(upc_ean) IN (12, 13) AND upc_ean NOT GLOB '*[^0-9]*'))
);

INSERT INTO items_new (id, title_id, owner_id, format, edition, upc_ean, location,
                       condition, acquired_on, visibility, notes, added_by,
                       created_at, updated_at, region, bluray_com_id, last_watched_on)
SELECT id, title_id, owner_id, format, edition, upc_ean, location, condition,
       acquired_on, visibility, notes, added_by, created_at, updated_at, region,
       bluray_com_id, last_watched_on
FROM   items;

DROP TABLE items;
ALTER TABLE items_new RENAME TO items;
CREATE UNIQUE INDEX ux_items_upc          ON items (upc_ean);
CREATE INDEX        ix_items_owner        ON items (owner_id);
CREATE INDEX        ix_items_title        ON items (title_id);
CREATE INDEX        ix_items_last_watched ON items (last_watched_on);

-- ------------------------------------------------------------------- views --
DROP VIEW IF EXISTS v_shared_library;
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

DROP VIEW IF EXISTS v_digital_sources;
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
SELECT u.id           AS owner_id,
       u.display_name AS owner,
       COUNT(i.id)    AS item_count,
       SUM(i.visibility = 'shared')  AS shared_count,
       SUM(i.visibility = 'private') AS private_count
FROM   users u
LEFT   JOIN items i ON i.owner_id = u.id
WHERE  u.is_active = 1
GROUP  BY u.id, u.display_name;

CREATE TRIGGER trg_users_block_delete_with_items
BEFORE DELETE ON users
WHEN EXISTS (SELECT 1 FROM items WHERE owner_id = OLD.id)
BEGIN
    SELECT RAISE(ABORT, 'user still owns items; deactivate instead of deleting');
END;

COMMIT;
PRAGMA foreign_keys = ON;
