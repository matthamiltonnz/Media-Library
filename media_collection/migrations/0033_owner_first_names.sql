-- Owner names read unevenly: Home Assistant hands over a person's full name
-- ("Matt Hamilton") while the seeded household members are already just
-- "Jeanine" and "Mitchell". The household is shown by first name, so shorten
-- the ones that carry more than that.
--
-- The two pseudo-users are not people and keep their label: `rips` is the
-- account every Jellyfin and file copy belongs to (and it is hidden from the
-- owner chips), `local` is what a browser without Home Assistant in front of
-- it becomes.
UPDATE users
   SET display_name = TRIM(SUBSTR(display_name, 1,
                                  INSTR(display_name || ' ', ' ') - 1))
 WHERE username NOT IN ('rips', 'local')
   AND INSTR(TRIM(display_name), ' ') > 0;
