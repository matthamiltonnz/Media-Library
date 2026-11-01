-- 0017_jellyfin_servers.sql
-- More than one Jellyfin server, each with its own URL and key, and every
-- link remembering which server it came from.
--
-- Today's single pair of settings becomes row 1, so an existing install
-- keeps its library without re-entering anything.

CREATE TABLE IF NOT EXISTS jellyfin_servers (
    id          INTEGER PRIMARY KEY,
    label       TEXT NOT NULL,
    url         TEXT NOT NULL,
    api_key     TEXT,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO jellyfin_servers (label, url, api_key)
SELECT 'Jellyfin',
       (SELECT value FROM settings WHERE key = 'jellyfin_url'),
       (SELECT value FROM settings WHERE key = 'jellyfin_api_key')
 WHERE EXISTS (SELECT 1 FROM settings WHERE key = 'jellyfin_url')
   AND NOT EXISTS (SELECT 1 FROM jellyfin_servers);

ALTER TABLE jellyfin_links ADD COLUMN server_id INTEGER
    REFERENCES jellyfin_servers(id);

UPDATE jellyfin_links
   SET server_id = (SELECT MIN(id) FROM jellyfin_servers)
 WHERE server_id IS NULL;

-- not unique: two copies of one film can point at the same Jellyfin item
CREATE INDEX IF NOT EXISTS ix_jellyfin_links_server
    ON jellyfin_links(server_id, jf_id);
