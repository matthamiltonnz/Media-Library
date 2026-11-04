"""Stores: where a copy came from, and the players that can open it.

A store is a *digital* shop (iTunes, Apple TV, Bandcamp) or a *retail* one (a
disc bought in a shop). The copy form has a single Store box - the format
decides which it means: a digital copy records the shop on its
`digital_sources` row (that row is also how the copy is reached), a physical
copy records it in `items.store_id`.

A store is also somewhere you can go to play it, and one shop is not one device:
the Apple TV app runs on the Apple TV, on an Android/Google TV and on a phone.
`store_targets` is that many-to-many, and the Play sheet lists every player
attached to a store. `playback.open_on()` then drives whichever kind it is -
a media_player, an input_select that fires an automation, a script, an
automation.

A 'system' store (jellyfin, rip) is a key the app writes itself and is never
offered in a picker. A key with no row at all still renders: old rows win.
"""

import re
from functools import cmp_to_key

import sorting

KINDS = ("digital", "retail", "system")
SYSTEM = ("jellyfin", "rip")
FALLBACK = {
    "itunes": "iTunes",
    "appletv": "Apple TV",
    "other": "other store",
    "jellyfin": "Jellyfin",
    "rip": "ripped file",
}

# The Android package behind an app, for the players that have no app list to
# pick from (an Android TV launches an app by package id, not by name). Only
# ids we have actually launched are listed; add more as they are confirmed -
# `adb shell pm list packages`, or the app's Play Store page (`?id=com.…`).
APP_CHOICES = (
    ("Apple TV", "com.apple.atve.androidtv.appletv"),
    ("Apple Music", "com.apple.android.music"),
    ("Google TV", "com.google.android.videos"),
    ("YouTube", "com.google.android.youtube"),
    ("Prime Video", "com.amazon.amazonvideo.livingroom"),
    ("Netflix", "com.netflix.ninja"),
    ("Disney+", "com.disney.disneyplus"),
    ("Spotify", "com.spotify.tv.android"),
    ("Plex", "com.plexapp.android"),
    ("Kodi", "org.xbmc.kodi"),
    ("Wiim", "com.linkplay.wiimtv"),
)

_SLUG = re.compile(r"[^a-z0-9]+")


def _dicts(con, sql, args=()):
    return [dict(row) for row in con.execute(sql, args).fetchall()]


def load(con, kind=None, active_only=False):
    """Stores: live shops in natural label order, then retired ones, then the
    system keys. `kind` narrows to 'digital' or 'retail'."""
    sql = "SELECT * FROM stores"
    if active_only:
        sql += " WHERE is_active = 1"
    rows = _dicts(con, sql)
    if kind:
        return [row for row in rows if row["kind"] == kind]
    shops = [row for row in rows if row["kind"] != "system"]
    rest = [row for row in rows if row["kind"] == "system"]
    shops.sort(key=cmp_to_key(lambda a, b: sorting.compare(a["label"], b["label"])))
    rest.sort(key=lambda row: row["key"])
    return ([row for row in shops if row["is_active"]] +
            [row for row in shops if not row["is_active"]] + rest)


def by_key(con):
    return {row["key"]: row for row in load(con)}


def by_id(con):
    return {row["id"]: row for row in load(con)}


def label(store_rows, key):
    """What to print for a provider key, even one that has no row."""
    row = store_rows.get(key) if isinstance(store_rows, dict) else None
    if row:
        return row["label"]
    return FALLBACK.get(key or "", key or "")


def suggested_app(label):
    """The package a store of this name usually runs as, when it is known."""
    want = (label or "").strip().lower()
    for name, package in APP_CHOICES:
        if name.lower() == want:
            return package
    return ""


def choices(con, kind="digital"):
    """(key, label) pairs a picker may offer, for one kind of shop."""
    return [(row["key"], row["label"])
            for row in load(con, kind=kind, active_only=True)]



def _slug(text):
    slug = _SLUG.sub("-", (text or "").lower()).strip("-")
    return slug or "store"


def _free_key(con, label_text):
    """A key nothing else uses, for a shop typed by hand."""
    taken = {row["key"] for row in load(con)}
    base = _slug(label_text)
    key, n = base, 1
    while key in taken:
        n += 1
        key = "%s-%d" % (base, n)
    return key


def resolve(con, value, kind="digital", commit=False):
    """The key for a typed shop, of the kind the copy's format calls for.

    Blank means no shop at all, and "Apple TV" matches 'appletv' however it is
    typed, so free text widens the list instead of making a second entry with a
    different spelling. The caller commits - this runs inside add_item().
    """
    text = (value or "").strip()
    if not text:
        return ""
    wanted = text.casefold()
    for row in load(con):
        if row["kind"] != "system" and (
                row["key"].casefold() == wanted
                or row["label"].casefold() == wanted):
            return row["key"]
    key = _free_key(con, text)
    con.execute("INSERT INTO stores (key, label, kind) VALUES (?, ?, ?)",
                (key, text, kind if kind in KINDS else "digital"))
    if commit:
        con.commit()
    return key


def add(con, label_text, kind="digital", app_name="", commit=False):
    """A new shop, or None when the label is blank."""
    label_text = (label_text or "").strip()
    if not label_text:
        return None
    key = _free_key(con, label_text)
    cur = con.execute(
        "INSERT INTO stores (key, label, kind, app_name) VALUES (?, ?, ?, ?)",
        (key, label_text, kind if kind in KINDS else "digital",
         (app_name or "").strip()))
    if commit:
        con.commit()
    return cur.lastrowid


def update(con, store_id, label_text=None, kind=None, app_name=None,
           is_active=None, commit=False):
    """Change what a shop is called, what it opens, and which kind it is."""
    sets, args = [], []
    if label_text is not None:
        label_text = label_text.strip()
        if not label_text:
            return False
        sets.append("label = ?")
        args.append(label_text)
    if kind in KINDS:
        sets.append("kind = ?")
        args.append(kind)
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
    """Drop a shop. Copies that used its key keep the key and still show the
    fallback wording, so deleting one loses nothing but its players."""
    con.execute("DELETE FROM stores WHERE id = ?", (store_id,))
    if commit:
        con.commit()


def targets(con):
    """Every player, whatever kind it is."""
    return _dicts(con, "SELECT * FROM playback_targets ORDER BY name COLLATE NOCASE")


def target(con, target_id):
    if not target_id:
        return None
    rows = _dicts(con, "SELECT * FROM playback_targets WHERE id = ?", (target_id,))
    return rows[0] if rows else None


def add_target(con, name, kind, ha_entity_id="", app_name="",
               input_source="", commit=False):
    """A player: an Apple TV, a Kodi box, an input_select or a script."""
    name = (name or "").strip()
    if not name:
        return None
    cur = con.execute(
        "INSERT OR IGNORE INTO playback_targets (name, kind, ha_entity_id,"
        " app_name, input_source) VALUES (?, ?, ?, ?, ?)",
        (name, kind or "media_player", (ha_entity_id or "").strip(),
         (app_name or "").strip(), (input_source or "").strip()))
    if commit:
        con.commit()
    return cur.lastrowid


def update_target(con, target_id, name=None, kind=None, ha_entity_id=None,
                  input_source=None, commit=False):
    """Rename a player, repoint it at another entity, or change its option.

    Only the values that are not None are written, so a form that carries a
    field the caller does not know about cannot blank it by accident.
    """
    fields = []
    args = []
    for column, value in (("name", name), ("kind", kind),
                          ("ha_entity_id", ha_entity_id),
                          ("input_source", input_source)):
        if value is None:
            continue
        fields.append("%s = ?" % column)
        args.append((value or "").strip())
    if not fields:
        return False
    args.append(int(target_id))
    con.execute("UPDATE playback_targets SET %s WHERE id = ?"
                % ", ".join(fields), tuple(args))
    if commit:
        con.commit()
    return True


def attached(con, store_id):
    """The players a shop can be opened on, each with the app to launch there."""
    return _dicts(
        con,
        "SELECT t.*, st.app AS app FROM store_targets st"
        "  JOIN playback_targets t ON t.id = st.target_id"
        " WHERE st.store_id = ? ORDER BY t.name COLLATE NOCASE",
        (store_id,))


def link_app(con, store_id, target_id):
    """The app to launch for one (shop, player) pair, or None if not attached."""
    rows = _dicts(con, "SELECT app FROM store_targets"
                        " WHERE store_id = ? AND target_id = ?",
                  (store_id, target_id))
    if not rows:
        return None
    return rows[0]["app"] or ""


def attach(con, store_id, target_id, app="", commit=False):
    con.execute("INSERT OR IGNORE INTO store_targets (store_id, target_id)"
                " VALUES (?, ?)", (store_id, target_id))
    if (app or "").strip():
        con.execute("UPDATE store_targets SET app = ?"
                    " WHERE store_id = ? AND target_id = ?",
                    (app.strip(), store_id, target_id))
    if commit:
        con.commit()


def detach(con, store_id, target_id, commit=False):
    con.execute("DELETE FROM store_targets WHERE store_id = ? AND target_id = ?",
                (store_id, target_id))
    if commit:
        con.commit()


def playable_stores(con, keys):
    """The shops among these keys that have a player attached.

    Returns [(store_row, [target rows], app_name)] in key order, which is what
    the Play sheet turns into one "On the ..." section per player.
    """
    rows = by_key(con)
    seen, found = set(), []
    for key in keys or ():
        if not key or key in seen or key in SYSTEM:
            continue
        store = rows.get(key)
        if not store:
            continue
        players = attached(con, store["id"])
        if not players:
            continue
        for player in players:
            # the app on the link first, then whatever the shop or the player
            # itself calls it - a link made before 0029 has only the latter
            player["app"] = (player.get("app") or store["app_name"]
                             or player.get("app_name") or "")
        seen.add(key)
        found.append((store, players, store["app_name"] or ""))
    return found
