-- 0010_jellyfin_links.sql
-- One row per catalogue copy: the Jellyfin item that holds the same file, so the
-- title page can offer "Play in Jellyfin" without re-matching on every request.

CREATE TABLE IF NOT EXISTS jellyfin_links (
    item_id    INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    jf_id      TEXT    NOT NULL,
    jf_kind    TEXT    NOT NULL
                       CHECK (jf_kind IN ('movie', 'album', 'season', 'series', 'episode')),
    jf_name    TEXT,
    jf_path    TEXT,
    matched_by TEXT    NOT NULL DEFAULT 'path' CHECK (matched_by IN ('path', 'name')),
    synced_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
