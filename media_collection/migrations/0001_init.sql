-- 0001_init.sql
-- Multi-owner media collection. Every physical copy ("item") has exactly one
-- owner; every active family member may see every shared item.
--
-- NOTE: SQLite enforces foreign keys per connection and defaults them OFF.
-- Every application connection must run `PRAGMA foreign_keys = ON;` (migrate.sh
-- does this). The trigger at the bottom enforces the owner rule even if a
-- connection forgets.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------- users ----

CREATE TABLE users (
    id            INTEGER PRIMARY KEY,
    username      TEXT    NOT NULL,              -- login handle, case-insensitive
    display_name  TEXT    NOT NULL,              -- "Mum", "Matt", ...
    password_hash TEXT,                          -- NULL = profile-only (no login)
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX ux_users_username ON users (lower(username));

-- --------------------------------------------------------------- titles ----
-- Work level (one row per film/series), independent of how many copies exist.

CREATE TABLE titles (
    id           INTEGER PRIMARY KEY,
    name         TEXT    NOT NULL,
    sort_name    TEXT,                           -- optional "Matrix, The"
    release_year INTEGER CHECK (release_year BETWEEN 1888 AND 2100),
    kind         TEXT    NOT NULL DEFAULT 'movie'
                         CHECK (kind IN ('movie', 'tv', 'other')),
    tmdb_id      INTEGER,                        -- metadata source used by the app
    notes        TEXT,
    created_by   INTEGER REFERENCES users (id) ON DELETE SET NULL,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX ux_titles_tmdb ON titles (tmdb_id) WHERE tmdb_id IS NOT NULL;
CREATE INDEX ix_titles_name ON titles (lower(name));

-- ---------------------------------------------------------------- items ----
-- Copy level: the SKU you scan. upc_ean is nullable because a real export had
-- 8 titles with no barcode (see upc-ean-missing-research.md).

CREATE TABLE items (
    id          INTEGER PRIMARY KEY,
    title_id    INTEGER NOT NULL REFERENCES titles (id) ON DELETE RESTRICT,
    owner_id    INTEGER NOT NULL REFERENCES users (id)  ON DELETE RESTRICT,
    format      TEXT    NOT NULL
                        CHECK (format IN ('dvd', 'bluray', 'bluray_3d', 'uhd_4k',
                                          'digital', 'vhs', 'other')),
    edition     TEXT,                            -- "Deluxe Edition", "Steelbook"
    upc_ean     TEXT,                            -- 12-digit UPC or 13-digit EAN
    location    TEXT,                            -- "Lounge shelf B"
    condition   TEXT,
    acquired_on TEXT,                            -- ISO-8601 date
    visibility  TEXT    NOT NULL DEFAULT 'shared'
                        CHECK (visibility IN ('shared', 'private')),
    notes       TEXT,
    added_by    INTEGER REFERENCES users (id) ON DELETE SET NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    CHECK (upc_ean IS NULL
           OR (length(upc_ean) IN (12, 13) AND upc_ean NOT GLOB '*[^0-9]*'))
);

CREATE UNIQUE INDEX ux_items_upc   ON items (upc_ean);
CREATE INDEX        ix_items_owner ON items (owner_id);
CREATE INDEX        ix_items_title ON items (title_id);

-- ---------------------------------------------------------------- views ----

-- Everything a signed-in family member is allowed to see.
CREATE VIEW v_shared_library AS
SELECT i.id            AS item_id,
       t.id            AS title_id,
       t.name          AS title,
       t.release_year,
       i.format,
       i.edition,
       i.upc_ean,
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

-- One row per owner: how much of the household collection each person holds.
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

-- ------------------------------------------------------------- triggers ----
-- Ownership is provenance: a user who still owns copies cannot be deleted
-- (deactivate instead). Enforced without relying on PRAGMA foreign_keys.

CREATE TRIGGER trg_users_block_delete_with_items
BEFORE DELETE ON users
WHEN EXISTS (SELECT 1 FROM items WHERE owner_id = OLD.id)
BEGIN
    SELECT RAISE(ABORT, 'user still owns items; deactivate instead of deleting');
END;
