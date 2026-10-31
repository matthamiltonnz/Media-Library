-- 0009_root_qnap.sql
-- One media root per Home Assistant media folder. The root stops at /media/QNAP and
-- the library subfolder (Music/, Movies/, TV/, ...) becomes part of the source path,
-- which is exactly what HA's `local` media source expects.
-- Physical copies also lose the NAS folder the scanners had written into `location`:
-- that column is for a shelf, not a file path.

PRAGMA foreign_keys = OFF;
BEGIN IMMEDIATE;

INSERT OR IGNORE INTO media_roots (label, path, medium, notes)
VALUES ('QNAP', '/media/QNAP', 'video',
        'Home Assistant media folder - Music/, Movies/, TV/ live inside');

-- The scanners used to copy the NAS folder into `location`; that column is a shelf.
UPDATE items
   SET location = NULL
 WHERE location IN ('Music', 'Videos', 'Matt''s Music', 'Jeanine''s Music',
                    'Justin''s Music', 'Mitchell''s Music');

UPDATE items
   SET location = NULL
 WHERE location IS NOT NULL
   AND location IN (SELECT DISTINCT substr(d.path, 1, instr(d.path, '/') - 1)
                      FROM digital_sources d
                     WHERE d.provider = 'rip' AND instr(d.path, '/') > 0);

UPDATE digital_sources
   SET path = (SELECT substr(r.path, length('/media/QNAP') + 2)
                 FROM media_roots r WHERE r.id = digital_sources.root_id)
              || '/' || path,
       root_id = (SELECT id FROM media_roots WHERE path = '/media/QNAP')
 WHERE root_id IN (SELECT id FROM media_roots WHERE path LIKE '/media/QNAP/%');

DELETE FROM media_roots
 WHERE path IN ('/media/QNAP/Music', '/media/QNAP/Movies', '/media/QNAP/iTunes Movies',
                '/media/QNAP/TV', '/media/QNAP/Mitchell''s Movies');

UPDATE items
   SET location = NULL
 WHERE location IN ('Music', 'Videos', 'Matt''s Music', 'Jeanine''s Music',
                    'Justin''s Music', 'Mitchell''s Music');

COMMIT;
PRAGMA foreign_keys = ON;
