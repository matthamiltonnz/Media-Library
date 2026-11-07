-- What one Jellyfin file actually is, in a line: "2160p Dolby Vision, DTS-HD MA
-- 5.1, 24 GB". The Play sheet needs it to say whether the file is worth playing
-- instead of getting the disc off the shelf - not every rip is the disc's equal.
-- Filled by expand_versions() during the sync, which already lists each item's
-- MediaSources and used to throw that answer away for single-file items.
ALTER TABLE digital_sources ADD COLUMN media_quality TEXT;
