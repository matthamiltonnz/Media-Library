-- A disc has no shop to open, but the room still has to be set up: the amp has
-- to change input, the player has to wake, a script has to run. That belongs to
-- the *format*, not to the shop the disc was bought in, so each physical format
-- gets a row of `stores` with kind 'format' and players hang off it through
-- store_targets, exactly like a shop.
--
-- The keys are `items.format` values and the labels match server.FORMAT_LABELS.
INSERT INTO stores (key, label, kind, is_active)
SELECT value, label, 'format', 1 FROM (
    SELECT 'bluray_3d' AS value, '3D Blu-ray' AS label
    UNION ALL SELECT 'bluray',    'Blu-ray'
    UNION ALL SELECT 'uhd_4k',    '4K Blu-ray'
    UNION ALL SELECT 'dvd',       'DVD'
    UNION ALL SELECT 'cd',        'CD'
    UNION ALL SELECT 'vinyl',     'Vinyl'
    UNION ALL SELECT 'sacd',      'SACD'
    UNION ALL SELECT 'cassette',  'Cassette'
    UNION ALL SELECT 'vhs',       'VHS'
    UNION ALL SELECT 'other',     'Other'
) WHERE NOT EXISTS (SELECT 1 FROM stores s WHERE s.key = value);
