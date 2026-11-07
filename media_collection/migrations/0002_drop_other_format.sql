-- "Other" was never a format: it sat in the add dialog's Format list and seeded a
-- row in `stores` so the admin could attach players to it, and no copy on the
-- shelf ever used it. The pickers no longer offer it (`server.FORMATS`), so the
-- row would only make the *Players by format* table list a format that cannot
-- occur. It goes - unless something already points at it, in which case it
-- stays and does no harm.
DELETE FROM stores
 WHERE key = 'other'
   AND kind = 'format'
   AND id NOT IN (SELECT store_id FROM items WHERE store_id IS NOT NULL)
   AND id NOT IN (SELECT store_id FROM store_targets);

-- A copy saved as "Other" before this release keeps its value: `FORMAT_LABELS`
-- still prints it as "Other" and the format editor keeps a value the picker no
-- longer offers rather than silently replacing it.
