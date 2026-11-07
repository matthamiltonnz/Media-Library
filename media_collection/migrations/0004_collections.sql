-- Film and TV collections: the shelf's series - Fast & Furious, Alien, the
-- Cornetto trilogy - as one row several titles point at, the way an album
-- points at its artist. A collection has its own page listing everything in
-- it, which is what a search cannot do: "Hobbs & Shaw" is the eighth Fast &
-- Furious film and shares none of its words.
--
-- `collection_order` is the place in the series, because a year is not always
-- enough (a prequel, two films in one year) and NULL means "not set", which
-- sorts after the numbered ones and then by year.
CREATE TABLE IF NOT EXISTS collections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL COLLATE NOCASE,
  sort_name TEXT,                   -- "The Abyss" files under A, as titles do
  kind TEXT NOT NULL DEFAULT 'movie',   -- 'movie' or 'tv'
  tmdb_id INTEGER,
  overview TEXT,                    -- what the series is, if anyone wrote it
  notes TEXT,                       -- the household's own note
  poster_url TEXT,                  -- its own picture; else a member's
  art_source TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS collections_name ON collections (name);

ALTER TABLE titles ADD COLUMN collection_id INTEGER
  REFERENCES collections(id) ON DELETE SET NULL;
ALTER TABLE titles ADD COLUMN collection_order INTEGER;

CREATE INDEX IF NOT EXISTS titles_collection
  ON titles (collection_id, collection_order, release_year);
