-- 0016_rip_on_the_copy.sql
-- A ripped copy is a playback source in its own right, so the file it points at
-- belongs on the copy: playback no longer has to join digital_sources, and a copy
-- that somehow ended up with two files can be split cleanly. digital_sources stays
-- as the history (provider, library id), these two columns are the live pointer.

ALTER TABLE items ADD COLUMN rip_path    TEXT;
ALTER TABLE items ADD COLUMN rip_root_id INTEGER REFERENCES media_roots (id);

UPDATE items SET
    rip_path    = (SELECT d.path    FROM digital_sources d
                    WHERE d.item_id = items.id AND d.provider = 'rip'
                    ORDER BY d.id LIMIT 1),
    rip_root_id = (SELECT d.root_id FROM digital_sources d
                    WHERE d.item_id = items.id AND d.provider = 'rip'
                    ORDER BY d.id LIMIT 1)
 WHERE EXISTS (SELECT 1 FROM digital_sources d
                WHERE d.item_id = items.id AND d.provider = 'rip');

CREATE INDEX IF NOT EXISTS ix_items_rip_path ON items (rip_root_id, rip_path);
