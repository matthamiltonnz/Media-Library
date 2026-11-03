"""Stores: where a digital copy came from, and the device that can open it.

`digital_sources.provider` holds the key ('itunes', 'appletv', 'jellyfin'), and
a row in `stores` says what to call it, whether the pickers offer it, and which
`playback_targets` device can open it. A store with a target is what puts
"On the Apple TV" in the title page's Play sheet - wake the device, open the
app - so a shop and a place to play from are the same record, which is what
iTunes and the Apple TV app actually are.

A 'system' store (jellyfin, rip) is a key the app writes itself and is never
offered in a picker. A key with no row at all still renders: old rows win.
"""

import re
from functools import cmp_to_key

import sorting

# keys the app writes itself, and the fallback wording if a row is missing
SYSTEM = ("jellyfin", "rip")
FALLBACK = {
    "itunes": "iTunes",
    "appletv": "Apple TV",
    "other": "other store",
    "jellyfin": "Jellyfin",
    "rip": "ripped file",
}

_SLUG = re.compile(r"[^a-z0-9]+")


def _dicts(con, sql, args=()):
    return [dict(row) for row in con.execute(sql, args).fetchall()]


def load(con, active_only=False):
    """Every store: live shops in natural label order, then retired ones, then
    the system keys."""
    sql = "SELECT * FROM stores"
    if active_only:
        sql += " WHERE is_active = 1"
    rows = _dicts(con, sql)
    shops = [row for row in rows if row["kind"] == "store"]
    rest = [row for row in rows if row["kind"] != "store"]
    shops.sort(key=cmp_to_key(lambda a, b: sorting.compare(a["label"], b["label"])))
    rest.sort(key=lambda row: row["key"])
    return ([row for row in shops if row["is_active"]] +
            [row for row in shops if not row["is_active"]] + rest)


def by_key(con):
    return {row["key"]: row for row in load(con)}


def label(store_rows, key):
    """What to print for a provider key, even one that has no row."""
    row = store_rows.get(key) if isinstance(store_rows, dict) else None
    if row:
        return row["label"]
    return FALLBACK.get(key or "", key or "")


def choices(con):
    """(key, label) pairs a picker may offer: live shops, not system keys."""
    return [(row["key"], row["label"])
            for row in load(con, active_only=True) if row["kind"] == "store"]



def _slug(text):
    slug = _SLUG.sub("-", (text or "").lower()).strip("-")
    return slug or "store"


def _free_key(con, label_text):
    """A key nothing else uses, for a store typed by hand."""
    taken = {row["key"] for row in load(con)}
    base = _slug(label_text)
    key, n = base, 1
    while key in taken:
        n += 1
        key = "%s-%d" % (base, n)
    return key


def resolve(con, value, commit=False):
    """The key for a typed store: an existing key or label, else a new row.

    Blank means no store at all, and "Apple TV" matches 'appletv' however it is
    typed, so free text widens the list instead of making a second entry with a
    different spelling. The caller commits - this runs inside add_item().
    """
    text = (value or "").strip()
    if not text:
        return ""
    wanted = text.casefold()
    for row in load(con):
        if row["key"].casefold() == wanted or row["label"].casefold() == wanted:
            return row["key"]
    key = _free_key(con, text)
    con.execute("INSERT INTO stores (key, label, kind) VALUES (?, ?, 'store')",
                (key, text))
    if commit:
        con.commit()
    return key


def add(con, label_text, target_id=None, app_name="", kind="store", commit=False):
    """A new store, or None when the label is blank."""
    label_text = (label_text or "").strip()
    if not label_text:
        return None
    key = _free_key(con, label_text)
    cur = con.execute(
        "INSERT INTO stores (key, label, kind, target_id, app_name)"
        " VALUES (?, ?, ?, ?, ?)",
        (key, label_text, kind, target_id or None, (app_name or "").strip()))
    if commit:
        con.commit()
    return cur.lastrowid


def update(con, store_id, label_text=None, target_id=None, app_name=None,
           is_active=None, commit=False):
    """Change what a store is called, and what it can open.

    An empty `target_id`/`app_name` clears it - that is a thing you do on
    purpose - while None leaves it alone.
    """
    sets, args = [], []
    if label_text is not None:
        label_text = label_text.strip()
        if not label_text:
            return False
        sets.append("label = ?")
        args.append(label_text)
    if target_id is not None:
        sets.append("target_id = ?")
        args.append(target_id or None)
    if app_name is not None:
        sets.append("app_name = ?")
        args.append((app_name or "").strip())
    if is_active is not None:
        sets.append("is_active = ?")
        args.append(1 if is_active else 0)
    if not sets:
        return False
    args.append(store_id)
    con.execute("UPDATE stores SET %s WHERE id = ?" % ", ".join(sets), args)
    if commit:
        con.commit()
    return True


def remove(con, store_id, commit=False):
    """Drop a store row. Copies that used its key keep the key and still show
    the fallback wording, so deleting one loses nothing."""
    con.execute("DELETE FROM stores WHERE id = ?", (store_id,))
    if commit:
        con.commit()


def targets(con):
    """The devices a store can open."""
    return _dicts(con, "SELECT * FROM playback_targets ORDER BY name COLLATE NOCASE")


def target(con, target_id):
    if not target_id:
        return None
    rows = _dicts(con, "SELECT * FROM playback_targets WHERE id = ?", (target_id,))
    return rows[0] if rows else None


def add_target(con, name, kind, ha_entity_id="", app_name="", commit=False):
    """A device to open a store on: an Apple TV, a Kodi box, a Jellyfin app."""
    name = (name or "").strip()
    if not name:
        return None
    cur = con.execute(
        "INSERT OR IGNORE INTO playback_targets (name, kind, ha_entity_id, app_name)"
        " VALUES (?, ?, ?, ?)",
        (name, kind, (ha_entity_id or "").strip(), (app_name or "").strip()))
    if commit:
        con.commit()
    return cur.lastrowid


def playable_stores(con, digital):
    """Stores among these copies that name a device to open them on.

    Returns [(store_row, target_row, app_name)], one per store, which is what
    the Play sheet turns into "On the Apple TV" sections.
    """
    rows = by_key(con)
    seen, found = set(), []
    for copy in digital or ():
        key = copy["provider"] if "provider" in copy.keys() else ""
        if not key or key in seen or key in SYSTEM:
            continue
        row = rows.get(key)
        if not row or not row["target_id"]:
            continue
        device = target(con, row["target_id"])
        if not device:
            continue
        seen.add(key)
        found.append((row, device, row["app_name"] or device["app_name"] or ""))
    return found
