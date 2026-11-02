-- MusicBrainz knows whether an act is a Person or a Group, which decides
-- whether the year is a birth year or a formation year.
ALTER TABLE artists ADD COLUMN kind TEXT;
