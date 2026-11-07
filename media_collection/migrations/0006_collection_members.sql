-- A collection's series on Wikidata, and the films that series holds.
--
-- The shelf knows which films it has; a series also lists the ones it has not.
-- `collections.wikidata_id` is the series item (P179 points *at* it, so it is
-- also the key for the reverse lookup), and `collection_members` is that
-- reverse lookup, kept so a page never waits on Wikimedia.
ALTER TABLE collections ADD COLUMN wikidata_id TEXT;

-- when the member list was last fetched, so the page can say how old it is
ALTER TABLE collections ADD COLUMN members_at TEXT;

CREATE TABLE IF NOT EXISTS collection_members (
    id INTEGER PRIMARY KEY,
    collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    wikidata_id TEXT NOT NULL,
    name TEXT NOT NULL,
    release_year INTEGER,
    imdb_id TEXT,
    UNIQUE (collection_id, wikidata_id)
);

CREATE INDEX IF NOT EXISTS collection_members_by_collection
    ON collection_members (collection_id);
