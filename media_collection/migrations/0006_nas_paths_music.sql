-- 0006_nas_paths_music.sql
-- The NAS is mounted at /media/QNAP inside the add-on (not /media/My Media/QNAP),
-- and the ripped music lives under /media/QNAP/Music.

PRAGMA foreign_keys = ON;

UPDATE media_roots SET path = '/media/QNAP/Movies'
 WHERE path = '/media/My Media/QNAP/Movies';
UPDATE media_roots SET path = '/media/QNAP/iTunes Movies'
 WHERE path = '/media/My Media/QNAP/iTunes Movies';
UPDATE media_roots SET path = '/media/QNAP/TV'
 WHERE path = '/media/My Media/QNAP/TV';
UPDATE media_roots SET path = '/media/QNAP/Mitchell''s Movies'
 WHERE path = '/media/My Media/QNAP/Mitchell''s Movies';

INSERT OR IGNORE INTO media_roots (label, path, medium, notes)
VALUES ('Music', '/media/QNAP/Music', 'music', 'Ripped albums, one folder per artist/album');
