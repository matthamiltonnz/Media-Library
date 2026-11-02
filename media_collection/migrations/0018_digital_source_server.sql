-- 0018_digital_source_server.sql
-- A route belongs to a Jellyfin server, not only a link: the copy's own
-- record has to say which server it can be played from.

ALTER TABLE digital_sources ADD COLUMN server_id INTEGER
    REFERENCES jellyfin_servers(id);

CREATE INDEX IF NOT EXISTS ix_digital_sources_server
    ON digital_sources(server_id, provider);
