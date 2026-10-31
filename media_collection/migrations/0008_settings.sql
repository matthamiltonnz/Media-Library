-- 0008_settings.sql
-- Small key/value store for the add-on's own preferences (last used player, and
-- anything a later integration needs to remember).

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
