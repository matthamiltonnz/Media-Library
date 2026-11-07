"""Film and TV collections - the shelf's series.

A collection is what an artist is for an album: one row that several titles
point at (`titles.collection_id`), with a page of its own listing all of them.
That page is the answer to a search problem: the eighth Fast & Furious film is
*Hobbs & Shaw* and shares not one word with the other seven.

`titles.collection_order` is the place in the series. A year usually sorts a
series correctly, so it is optional - but a prequel, or two films in one year,
needs it, and a collection whose titles are numbered is shown numbered.
"""

import artists
import re
import sorting


SERIES_KINDS = ("movie", "tv")


def _order(alias):
    """Numbered titles first, then by year, then by name."""
    return ("%s.collection_order IS NULL, %s.collection_order,"
            " %s.release_year," % (alias, alias, alias))


def all_collections(con, viewer_id, query=""):
    """Every collection, for the list page - an empty one included.

    A collection is made before its films are put in it, so filtering to the
    ones that hold something would make a brand-new collection vanish from
    the list it was made on.
    """
    sorting.register(con)      # NATSORT is per connection
    like = "%" + (query or "") + "%"
    return con.execute(
        "SELECT c.*,"
        " (SELECT COUNT(DISTINCT t.id) FROM titles t"
        "    JOIN items i ON i.title_id = t.id"
        "   WHERE t.collection_id = c.id"
        "     AND (i.visibility = 'shared' OR i.owner_id = ?)) AS films,"
        " (SELECT MIN(t.release_year) FROM titles t"
        "   WHERE t.collection_id = c.id) AS first_year,"
        " (SELECT MAX(t.release_year) FROM titles t"
        "   WHERE t.collection_id = c.id) AS last_year,"
        " (SELECT t3.poster_url FROM titles t3"
        "   WHERE t3.collection_id = c.id AND t3.poster_url IS NOT NULL"
        "   ORDER BY " + _order("t3") + " t3.id LIMIT 1) AS member_poster,"
        " (SELECT l.jf_id FROM titles t2"
        "    JOIN items i2 ON i2.title_id = t2.id"
        "    JOIN jellyfin_links l ON l.item_id = i2.id"
        "   WHERE t2.collection_id = c.id"
        "   ORDER BY " + _order("t2") + " t2.id LIMIT 1) AS jf_id,"
        " (SELECT group_concat(DISTINCT i4.owner_id) FROM titles t4"
        "    JOIN items i4 ON i4.title_id = t4.id"
        "   WHERE t4.collection_id = c.id) AS owner_ids"
        " FROM collections c"
        " WHERE (? = '' OR c.name LIKE ?)"
        " ORDER BY COALESCE(NULLIF(c.sort_name, ''), c.name) COLLATE NATSORT,"
        "          c.name COLLATE NATSORT",
        (viewer_id, query or "", like)).fetchall()


def get(con, collection_id):
    """One collection, carrying the same cover fields the list page has.

    The page shows the collection's own picture, or the first film's; asking
    for it here rather than at the call site keeps the list and the page
    showing the same cover.
    """
    sorting.register(con)
    return con.execute(
        "SELECT c.*,"
        " (SELECT t3.poster_url FROM titles t3"
        "   WHERE t3.collection_id = c.id AND t3.poster_url IS NOT NULL"
        "   ORDER BY " + _order("t3") + " t3.id LIMIT 1) AS member_poster,"
        " (SELECT l.jf_id FROM titles t2"
        "    JOIN items i2 ON i2.title_id = t2.id"
        "    JOIN jellyfin_links l ON l.item_id = i2.id"
        "   WHERE t2.collection_id = c.id"
        "   ORDER BY " + _order("t2") + " t2.id LIMIT 1) AS jf_id"
        " FROM collections c WHERE c.id = ?", (collection_id,)).fetchone()


def titles_for(con, collection_id, viewer_id):
    """The films or series in one collection, in the order they are watched."""
    sorting.register(con)
    return con.execute(
        "SELECT t.*, COUNT(i.id) AS copies,"
        " group_concat(DISTINCT i.format) AS formats,"
        " MIN(i.last_watched_on) AS last_watched_on,"
        " (SELECT l.jf_id FROM jellyfin_links l"
        "    JOIN items i2 ON i2.id = l.item_id"
        "   WHERE i2.title_id = t.id ORDER BY l.jf_id LIMIT 1) AS jf_id"
        " FROM titles t JOIN items i ON i.title_id = t.id"
        " WHERE t.collection_id = ?"
        "   AND (i.visibility = 'shared' OR i.owner_id = ?)"
        " GROUP BY t.id"
        " ORDER BY t.collection_order IS NULL, t.collection_order,"
        "          t.release_year,"
        "          COALESCE(NULLIF(t.sort_name, ''), t.name) COLLATE NATSORT",
        (collection_id, viewer_id)).fetchall()


def choices(con, kind=""):
    """Every collection, for the box on a title's page."""
    sorting.register(con)
    sql = ("SELECT id, name, kind FROM collections"
           " ORDER BY COALESCE(NULLIF(sort_name, ''), name) COLLATE NATSORT")
    params = ()
    if kind in SERIES_KINDS:
        sql = ("SELECT id, name, kind FROM collections WHERE kind = ?"
               " ORDER BY COALESCE(NULLIF(sort_name, ''), name) COLLATE NATSORT")
        params = (kind,)
    return con.execute(sql, params).fetchall()


def resolve(con, name, kind="movie"):
    """The id of the collection called `name`, created if it is new.

    A collection is named on the title page as often as it is made on the
    collections page, so a typed name has to work there too. The column is
    COLLATE NOCASE, so "fast & furious" finds "Fast & Furious".
    """
    name = " ".join((name or "").split())
    if not name:
        return None
    found = con.execute("SELECT id FROM collections WHERE name = ?",
                        (name,)).fetchone()
    if found:
        return found["id"]
    made = con.execute(
        "INSERT INTO collections (name, sort_name, kind) VALUES (?, ?, ?)",
        (name, artists.sort_name(name),
         kind if kind in SERIES_KINDS else "movie"))
    con.commit()
    return made.lastrowid


def link_title(con, title_id, collection_id, order=None):
    """Put one title in a collection, at `order` if it was given."""
    if order in ("", None):
        order = None
    else:
        order = int(order)
    con.execute("UPDATE titles SET collection_id = ?, collection_order = ?"
                " WHERE id = ?", (collection_id, order, title_id))
    con.commit()


def unlink_title(con, title_id):
    con.execute("UPDATE titles SET collection_id = NULL,"
                " collection_order = NULL WHERE id = ?", (title_id,))
    con.commit()


def rename(con, collection_id, name):
    name = " ".join((name or "").split())
    if not name:
        return
    con.execute("UPDATE collections SET name = ?, sort_name = ? WHERE id = ?",
                (name, artists.sort_name(name), collection_id))
    con.commit()


def set_notes(con, collection_id, text):
    con.execute("UPDATE collections SET overview = ? WHERE id = ?",
                ((text or "").strip() or None, collection_id))
    con.commit()


def fill_details(con, collection_id, overview="", poster=""):
    """Fill a collection's own fields from its series' article.

    Only where they are empty, and it returns what it filled: a paragraph
    somebody typed and a cover somebody chose both win over a scrape, and the
    page says which of the two was missing rather than "done".
    """
    row = con.execute("SELECT overview, poster_url FROM collections"
                      " WHERE id = ?", (collection_id,)).fetchone()
    if row is None:
        return []
    filled = []
    if overview and not (row["overview"] or "").strip():
        con.execute("UPDATE collections SET overview = ? WHERE id = ?",
                    (overview.strip(), collection_id))
        filled.append("paragraph")
    if poster and not (row["poster_url"] or "").strip():
        con.execute("UPDATE collections SET poster_url = ?,"
                    " art_source = 'poster' WHERE id = ?",
                    (poster, collection_id))
        filled.append("cover")
    con.commit()
    return filled


def remove(con, collection_id):
    """Delete the collection; its titles stay on the shelf, uncollected."""
    con.execute("UPDATE titles SET collection_id = NULL,"
                " collection_order = NULL WHERE collection_id = ?",
                (collection_id,))
    con.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
    con.commit()


def matching(con, viewer_id, query, owner=None):
    """The collections whose name matches `query`, for the catalogue's results.

    A search for "Fast & Furious" should offer the series itself, not only the
    eleven films inside it - that is the whole point of a collection.
    """
    needle = (query or "").strip().lower()
    if not needle:
        return []
    found = []
    for row in all_collections(con, viewer_id):
        if needle not in (row["name"] or "").lower():
            continue
        if owner and str(owner) not in (row["owner_ids"] or "").split(","):
            continue
        found.append(row)
    return found


def candidates(con, term, viewer_id, collection_id=None, limit=40):
    """Films and series this viewer holds that are not in this collection.

    The box exists for the title a search cannot find - *Hobbs & Shaw* is in
    the Fast & Furious collection and shares none of its words - so a typed
    term is the normal way in; an empty one lists the shelf alphabetically,
    capped, and each hit says when another collection already holds it.
    """
    sorting.register(con)
    term = (term or "").strip()
    return con.execute(
        "SELECT t.id, t.name, t.release_year, t.kind,"
        " (SELECT COUNT(*) FROM items i2 WHERE i2.title_id = t.id) AS copies,"
        " (SELECT c2.name FROM collections c2 WHERE c2.id = t.collection_id)"
        " AS in_collection"
        " FROM titles t"
        " WHERE t.kind IN ('movie', 'tv')"
        "   AND EXISTS (SELECT 1 FROM items i WHERE i.title_id = t.id"
        "               AND (i.visibility = 'shared' OR i.owner_id = ?))"
        "   AND (t.collection_id IS NULL"
        "        OR t.collection_id <> COALESCE(?, -1))"
        "   AND (? = '' OR t.name LIKE ?)"
        " ORDER BY t.name COLLATE NATSORT, t.release_year"
        " LIMIT ?",
        (viewer_id, collection_id, term, "%" + term + "%", limit)).fetchall()


def remember_series(con, collection_id, qid):
    """Store the Wikidata item a collection stands for - the first answer wins.

    An id already set (by an earlier lookup, or by hand) is never replaced: the
    films in a series are found by pointing *at* this item, so a wrong id would
    silently list the wrong films.
    """
    qid = (qid or "").strip()
    if not qid:
        return
    con.execute("UPDATE collections SET wikidata_id = ?"
                " WHERE id = ? AND (wikidata_id IS NULL OR wikidata_id = '')",
                (qid, collection_id))
    con.commit()


def members(con, collection_id):
    """The films Wikidata files under this collection's series, oldest first."""
    return con.execute(
        "SELECT * FROM collection_members WHERE collection_id = ?"
        " ORDER BY COALESCE(release_year, 9999), name COLLATE NOCASE",
        (collection_id,)).fetchall()


def store_members(con, collection_id, rows):
    """Replace a collection's member list with a fresh lookup."""
    con.execute("DELETE FROM collection_members WHERE collection_id = ?",
                (collection_id,))
    for item in rows:
        con.execute(
            "INSERT OR IGNORE INTO collection_members"
            " (collection_id, wikidata_id, name, release_year, imdb_id)"
            " VALUES (?, ?, ?, ?, ?)",
            (collection_id, item["qid"], item["name"],
             item.get("year"), item.get("imdb_id")))
    con.execute("UPDATE collections SET members_at = datetime('now')"
                " WHERE id = ?", (collection_id,))
    con.commit()


def file_members(con, collection_id):
    """File uncollected shelf films that this series knows about.

    A member list names the films of a series with their years, so a film
    sitting uncollected on the shelf - *Hobbs & Shaw* beside ten Fast & Furious
    discs - can be filed from it without asking anybody. Only titles with **no**
    collection are touched: a film somebody filed elsewhere is never moved,
    which is `autofill_series()`'s rule too and for the same reason. Returns the
    names it filed, so the caller can say so.
    """
    head = con.execute("SELECT kind FROM collections WHERE id = ?",
                       (collection_id,)).fetchone()
    if head is None or (head["kind"] or "") not in SERIES_KINDS:
        return []
    rows = members(con, collection_id)
    if not rows:
        return []
    loose = con.execute(
        "SELECT id, name, release_year, kind FROM titles"
        " WHERE collection_id IS NULL AND kind = ?"
        " ORDER BY name", (head["kind"],)).fetchall()
    filed = []
    for title in loose:
        for item in rows:
            if not _same_film(title["name"], title["release_year"],
                              item["name"], item["release_year"]):
                continue
            con.execute("UPDATE titles SET collection_id = ? WHERE id = ?",
                        (collection_id, title["id"]))
            filed.append(title["name"])
            break
    if filed:
        con.commit()
    return filed


# superscript digits, because Wikidata writes "Alien 3" for the shelf's "Alien³"
_SUPERSCRIPT = {"\u2070": "0", "\u00b9": "1", "\u00b2": "2", "\u00b3": "3",
                "\u2074": "4", "\u2075": "5", "\u2076": "6", "\u2077": "7",
                "\u2078": "8", "\u2079": "9"}


def _key(name):
    """A case- and punctuation-blind name key: 'Hobbs & Shaw' -> 'hobbsshaw'.

    '&' counts as 'and' and a leading 'the' is dropped, because Wikidata and a
    shop's sleeve disagree about both ("Fast & Furious" is the same film as
    "Fast and Furious", and the shelf files "The Fast and the Furious").
    """
    text = "".join(_SUPERSCRIPT.get(ch, ch) for ch in (name or "")).lower()
    text = re.sub(r"^the\s+", "", text.replace("&", " and ").strip())
    return re.sub(r"[^a-z0-9]+", "", text)


def same_name(name_a, name_b):
    """Two series names that mean the same thing ('Fast & Furious' == 'fast furious')."""
    return bool(_key(name_a)) and _key(name_a) == _key(name_b)


def _same_film(name_a, year_a, name_b, year_b):
    """Same film: the same name, and years that do not contradict each other."""
    if not _key(name_a) or _key(name_a) != _key(name_b):
        return False
    if year_a and year_b:
        return abs(int(year_a) - int(year_b)) <= 1
    return True


def series_summary(con, collection_id, viewer_id=None):
    """What the series holds, split into what the shelf has and what it lacks.

    A member counts as owned when a title on the shelf matches it: first by name
    (and a year within one), then - because Wikidata and a sleeve disagree about
    titles, "Fast Five" against "Fast & Furious 5" - by a year that only one film
    on each side claims. Whatever is left is the list a collector wants: the
    films in the series nobody here owns.
    """
    head = con.execute("SELECT wikidata_id, members_at FROM collections"
                       " WHERE id = ?", (collection_id,)).fetchone()
    if head is None:
        return {"qid": "", "checked": "", "count": 0,
                "missing": [], "elsewhere": []}
    rows = members(con, collection_id)
    if not rows:
        # nothing fetched yet: do not read the whole shelf just to say so
        return {"qid": head["wikidata_id"] or "",
                "checked": head["members_at"] or "",
                "count": 0, "missing": [], "elsewhere": []}
    shelf = con.execute("SELECT DISTINCT t.id, t.name, t.release_year,"
                        "       t.collection_id"
                        "  FROM titles t JOIN items i ON i.title_id = t.id"
                        " WHERE (i.visibility = 'shared' OR i.owner_id = ?)",
                        (int(viewer_id or 0),)).fetchall()

    matched, used = {}, set()
    for item in rows:                   # 1. by name, anywhere on the shelf
        for title in shelf:
            if title["id"] in used:
                continue
            if _same_film(item["name"], item["release_year"],
                          title["name"], title["release_year"]):
                matched[item["wikidata_id"]] = title
                used.add(title["id"])
                break
    for item in rows:                   # 2. by a year nothing else claims
        if item["wikidata_id"] in matched or not item["release_year"]:
            continue
        # only films in *this* collection: a 2011 release somewhere else
        # on the shelf says nothing about which series film it is
        free = [title for title in shelf
                if title["id"] not in used
                and title["collection_id"] == collection_id
                and title["release_year"] == item["release_year"]]
        rivals = [other for other in rows
                  if other["wikidata_id"] not in matched
                  and other["release_year"] == item["release_year"]]
        if len(free) == 1 and len(rivals) == 1:
            matched[item["wikidata_id"]] = free[0]
            used.add(free[0]["id"])

    missing, elsewhere = [], []
    for item in rows:
        title = matched.get(item["wikidata_id"])
        if title is not None and title["collection_id"] == collection_id:
            continue                    # already on the wall
        entry = {"name": item["name"], "year": item["release_year"],
                 "imdb_id": item["imdb_id"], "qid": item["wikidata_id"]}
        if title is None:
            missing.append(entry)
        else:
            entry["title_id"] = title["id"]
            elsewhere.append(entry)
    return {"qid": head["wikidata_id"] or "",
            "checked": head["members_at"] or "",
            "count": len(rows), "missing": missing, "elsewhere": elsewhere}
