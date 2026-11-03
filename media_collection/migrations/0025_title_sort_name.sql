-- 0025_title_sort_name.sql
-- titles.sort_name has been in the schema since 0001 and was NULL on every
-- row, so the catalogue ordered by t.name: "The A-Team" filed under T while the
-- act list already put "The Beatles" under B through artists.sort_name.
--
-- Same convention here - the article moves to the end, so "The Matrix" sorts as
-- "Matrix, The" - and a pair of triggers keeps the column filled however a
-- title is written (the add dialog, film_import, music_import, repair, or a
-- rename on the title page), so no insert path has to remember.

UPDATE titles SET sort_name = CASE
    WHEN lower(substr(name, 1, 4)) = 'the ' THEN substr(name, 5) || ', The'
    WHEN lower(substr(name, 1, 2)) = 'a '   THEN substr(name, 3) || ', A'
    WHEN lower(substr(name, 1, 3)) = 'an '  THEN substr(name, 4) || ', An'
    ELSE name
  END
WHERE sort_name IS NULL OR sort_name = '';

CREATE TRIGGER IF NOT EXISTS titles_sort_name_on_insert
AFTER INSERT ON titles
FOR EACH ROW
WHEN NEW.sort_name IS NULL OR NEW.sort_name = ''
BEGIN
  UPDATE titles SET sort_name = CASE
      WHEN lower(substr(NEW.name, 1, 4)) = 'the ' THEN substr(NEW.name, 5) || ', The'
      WHEN lower(substr(NEW.name, 1, 2)) = 'a '   THEN substr(NEW.name, 3) || ', A'
      WHEN lower(substr(NEW.name, 1, 3)) = 'an '  THEN substr(NEW.name, 4) || ', An'
      ELSE NEW.name
    END
  WHERE id = NEW.id;
END;

-- AFTER UPDATE OF name, so the migration's own UPDATE (which sets sort_name
-- only) cannot fire it, and a re-save that keeps the name is left alone.
CREATE TRIGGER IF NOT EXISTS titles_sort_name_on_rename
AFTER UPDATE OF name ON titles
FOR EACH ROW
WHEN NEW.name <> OLD.name
BEGIN
  UPDATE titles SET sort_name = CASE
      WHEN lower(substr(NEW.name, 1, 4)) = 'the ' THEN substr(NEW.name, 5) || ', The'
      WHEN lower(substr(NEW.name, 1, 2)) = 'a '   THEN substr(NEW.name, 3) || ', A'
      WHEN lower(substr(NEW.name, 1, 3)) = 'an '  THEN substr(NEW.name, 4) || ', An'
      ELSE NEW.name
    END
  WHERE id = NEW.id;
END;
