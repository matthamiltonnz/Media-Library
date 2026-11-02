-- One Jellyfin item can carry several files: Almost Famous has the theatrical
-- 2160p, the Extended 2160p and a 1080p TrueHD in a single item. Record which
-- MediaSource a route points at, so each version can be its own copy and be
-- started on its own.
ALTER TABLE digital_sources ADD COLUMN media_source_id TEXT;
ALTER TABLE digital_sources ADD COLUMN media_source_name TEXT;
