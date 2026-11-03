-- 0029_shop_apps.sql
-- The app belongs to the (shop, player) pair, not to either on its own: the
-- Apple TV app is "Apple TV" on a tvOS box and com.apple.atve.androidtv.appletv
-- on the Android TV in the bedroom, and that same Android TV shows a different
-- package again for Apple Music or Google TV. So the app moves onto the link.
--
-- playback_targets.app_name stays as a fallback for links made before this, and
-- the backfill copies it across so nothing that worked stops working.

ALTER TABLE store_targets ADD COLUMN app TEXT;

UPDATE store_targets
   SET app = (SELECT t.app_name FROM playback_targets t
               WHERE t.id = store_targets.target_id)
 WHERE app IS NULL OR app = '';

-- Shops whose films and music live in an Android TV app. Google Play Movies &
-- TV is the Google TV app now (com.google.android.videos); its purchases are
-- still there and in YouTube.
INSERT OR IGNORE INTO stores (key, label, kind) VALUES
    ('applemusic', 'Apple Music', 'digital'),
    ('googletv',   'Google TV',   'digital');
