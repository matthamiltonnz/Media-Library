-- Google Play Music shut down in 2020 and its library moved to YouTube Music,
-- so a downloaded file that came from it belongs to the shop that replaced it.
-- A literal "Google Play Music" row can still be added from the admin page
-- (Add a store) when the provenance matters more than the successor does.
INSERT INTO stores (key, label, kind, is_active)
SELECT 'youtubemusic', 'YouTube Music', 'digital', 1
 WHERE NOT EXISTS (SELECT 1 FROM stores WHERE key = 'youtubemusic');
