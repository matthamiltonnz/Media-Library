#!/usr/bin/env python3
"""Media Collection - Home Assistant add-on.

Stdlib only (the base image ships python3 + sqlite3). Serves a server-rendered
page behind Home Assistant ingress; the HA user making the request becomes the
collection owner via the X-Remote-User-* headers the Supervisor forwards.
"""
import csv
import glob
import html
import io
import json
import os
import re
import sqlite3
import socket
import ssl
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from string import Template
from urllib.parse import parse_qs, quote, unquote, urlparse

import dedupe
import jellyfin
import metadata
import playback
import clean_job
import refresh_job
import scan
import artists
import boxsets
import ui
import ui_artists
import ui_boxsets
import ui_admin
import ui_title
import sorting
import stores

PORT = int(os.environ.get("PORT", "8099"))
DB_PATH = os.environ.get("DB_PATH", "/data/media.db")
OPTIONS_PATH = "/data/options.json"
COVERS_DIR = os.environ.get("COVERS_DIR") or os.path.join(
    os.path.dirname(DB_PATH), "covers")
COVER_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
              ".png": "image/png", ".gif": "image/gif",
              ".webp": "image/webp"}
MAX_COVER_BYTES = 6 * 1024 * 1024

# One add at a time. A POST that starts a title then spends seconds looking
# its metadata up holds this, so three impatient clicks cannot each pass the
# duplicate check before the first row is committed - which is how *Let the
# music play* arrived three times.
ADD_LOCK = threading.Lock()

FORMATS = ["bluray_3d", "bluray", "uhd_4k", "dvd", "digital", "ripped", "cd", "vinyl", "sacd",
           "cassette", "vhs"]
# "other" is not one of them: a disc with no proper format is an unknown, not a
# category, and it only ever became one because the picker offered it. The label
# stays in FORMAT_LABELS so a copy saved before 0.23.144 still prints.
# A digital copy's store is a row in `stores` (see stores.py); a ripped file
# has none.

FORMAT_LABELS = {
    "bluray_3d": "3D Blu-ray",
    "bluray": "Blu-ray",
    "uhd_4k": "4K Blu-ray",
    "dvd": "DVD",
    "digital": "Digital",
    "ripped": "Ripped file",
    "cd": "CD",
    "vinyl": "Vinyl",
    "sacd": "SACD",
    "cassette": "Cassette",
    "vhs": "VHS",
    "other": "Other",
    "rip": "Ripped file",
}
KINDS = {"movie": "Movie", "tv": "TV", "music": "Music", "other": "Other"}
# the case drawn around the artwork in the thumbnail view, by the best copy held
CASE_ORDER = {
    "music": ("cd", "vinyl", "digital", "bluray", "dvd", "ripped"),
    "movie": ("uhd_4k", "digital", "bluray", "bluray_3d", "dvd", "vhs", "ripped"),
    "tv": ("uhd_4k", "digital", "bluray", "bluray_3d", "dvd", "vhs", "ripped"),
}
CASE_CLASS = {"uhd_4k": "case-4k", "bluray_3d": "case-3d", "bluray": "case-blu",
              "dvd": "case-dvd", "cd": "case-jewel", "vinyl": "case-sleeve",
              "digital": "case-digital", "ripped": "case-digital"}

# the shelf order: best picture first, a file on a media folder always last
QUALITY = {"uhd_4k": 0, "digital": 1, "bluray": 2, "bluray_3d": 3, "dvd": 4,
           "cd": 5, "vinyl": 6, "sacd": 7, "cassette": 8, "vhs": 9,
           "other": 10, "ripped": 11}


def quality_sorted(formats):
    """A title's formats, best first — 4K, digital, Blu-ray, DVD, VHS, ripped."""
    return sorted((f for f in (formats or "").split(",") if f),
                  key=lambda f: QUALITY.get(f, 99))


def shown_formats(formats, picked=()):
    """What a card draws: the best of the formats currently being filtered."""
    order = quality_sorted(formats)
    chosen = [f for f in order if not picked or f in picked]
    return ",".join(chosen or order)


CASE_LABEL = {CASE_CLASS["uhd_4k"]: "4K Ultra HD",
              CASE_CLASS["bluray_3d"]: "Blu-ray 3D",
              CASE_CLASS["bluray"]: "Blu-ray",
              CASE_CLASS["dvd"]: "DVD"}


def case_band(kind, formats, shops=""):
    """The words printed across the top of the case, or '' for a case without one."""
    case = case_class(kind, formats)
    for name, label in CASE_LABEL.items():
        if name in case:
            return '<span class="band">%s</span>' % html.escape(label)
    if case.startswith("case-digital"):
        # a file has no printed band, but the white one keeps the shelf
        # aligned - and it can say where the copy came from ("Apple Music"),
        # which is more use on the shelf than the word "Digital".
        have = set(filter(None, (formats or "").split(",")))
        for fmt in ("digital", "ripped"):
            if fmt in have:
                label = (shops or "").replace(",", " \u00b7 ")
                return ('<span class="band plain">%s</span>'
                        % html.escape(label or FORMAT_LABELS.get(fmt, fmt)))
    return ""


def case_class(kind, formats):
    """Virtual case for a card: jewel case, sleeve, or blue/yellow/black Blu-ray."""
    have = set(filter(None, (formats or "").split(",")))
    for fmt in CASE_ORDER.get(kind, CASE_ORDER["movie"]):
        if fmt in have:
            return CASE_CLASS[fmt] + (" case-music" if kind == "music" else "")
    return CASE_CLASS["digital"] + (" case-music" if kind == "music" else "")
# the top-level switch: the catalogue is always in exactly one of these
MODES = (("movie", "Movies"), ("tv", "TV"), ("music", "Music"))


def load_options():
    try:
        with open(OPTIONS_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def refresh_hours():
    """How often the automatic metadata pass runs: 24 daily, 168 weekly, 0 off.

    Read from the add-on option, so the household can change it on the
    Configuration tab without touching the app; MC_REFRESH_HOURS overrides it
    for a developer running the code directly.
    """
    value = os.environ.get("MC_REFRESH_HOURS")
    if value is None:
        value = load_options().get("refresh_hours", 24)
    try:
        return max(0, min(720, int(value)))
    except (TypeError, ValueError):
        return 24


OPTIONS = load_options()
DEFAULT_VISIBILITY = OPTIONS.get("default_visibility", "shared")
LOG_LEVEL = OPTIONS.get("log_level", "info")
RIPPED_ROOT = OPTIONS.get("ripped_movies_path", "/media/My Media/QNAP/Movies")


def log(msg):
    if not msg.startswith("debug:") or LOG_LEVEL == "debug":
        print(msg, flush=True)


def connect():
    # WAL plus a background sync: ten seconds was not always enough, and a
    # lost lock surfaces as a dropped request, not as an error page
    con = sqlite3.connect(DB_PATH, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 60000")
    con.execute("PRAGMA foreign_keys = ON")
    # NATSORT compares digit runs by value, so "2 Fast" files before "10,000"
    sorting.register(con)
    return con
def with_players(con):
    """Every store with the players attached to it, for the admin panel."""
    rows = stores.load(con)
    for row in rows:
        row["players"] = stores.attached(con, row["id"])
    return rows


def store_keys(con, digital, items):
    """Every shop behind these copies, for the Play sheet.

    A digital copy carries its shop on the source row (`provider`), a disc on
    the copy itself (`items.store_id`), so both are walked.
    """
    keys = [row["provider"] for row in digital or ()]
    shops = stores.by_id(con)
    for copy in items or ():
        store_id = copy["store_id"] if "store_id" in copy.keys() else None
        shop = shops.get(store_id) if store_id else None
        if shop:
            keys.append(shop["key"])
        # A disc is played by putting it in, but the room can still be set up
        # first: a format row (kind 'format') carries the players for Blu-ray,
        # DVD, vinyl and so on, whatever shop the copy came from.
        fmt = copy["format"] if "format" in copy.keys() else ""
        if fmt and fmt not in ("digital", "ripped"):
            keys.append(fmt)
    return keys





# ------------------------------------------------------------------ users ----

def short_name(text):
    """The name to show for a person: their first name.

    Home Assistant hands over a full name ("Matt Hamilton") while the seeded
    household members are already just "Jeanine" and "Mitchell", so the owner
    chips read unevenly. The two pseudo-users are not people and keep their
    label.
    """
    text = (text or "").strip()
    if not text or text in ("Ripped files", "Local user"):
        return text
    return text.split()[0]


def ensure_user(con, username, display_name):
    """Create the row on first sight, so a new HA user needs no admin step."""
    display_name = short_name(display_name)
    con.execute(
        "INSERT OR IGNORE INTO users (username, display_name) VALUES (?, ?)",
        (username, display_name),
    )
    if display_name:
        # Home Assistant is the source for the name: a person renamed there
        # ("Matt" -> "Matthew") is picked up on their next request. INSERT OR
        # IGNORE alone would leave the first name it ever saw.
        con.execute("UPDATE users SET display_name = ?"
                    " WHERE lower(username) = lower(?) AND display_name <> ?",
                    (display_name, username, display_name))
    con.commit()
    return con.execute(
        "SELECT id, display_name FROM users WHERE lower(username) = lower(?)",
        (username,),
    ).fetchone()


def viewer_for(handler, con):
    ha_id = (handler.headers.get("X-Remote-User-Id") or "").strip()
    name = (
        handler.headers.get("X-Remote-User-Display-Name")
        or handler.headers.get("X-Remote-User-Name")
        or ""
    ).strip()
    if not ha_id:
        # No ingress (e.g. curl straight at the add-on): single local profile.
        return ensure_user(con, "local", name or "Local user")
    safe = re.sub(r"[^A-Za-z0-9_-]", "", ha_id) or "unknown"
    return ensure_user(con, "ha-" + safe, name or "Home Assistant user")


# ------------------------------------------------------------------ views ----

def library_rows(con, viewer_id, query="", kind="", owner=None, format=""):
    sorting.register(con)      # NATSORT is per connection; see sorting.py
    sql = """
        SELECT i.id, t.id AS title_id, t.name AS title, t.release_year, i.format,
               i.edition, i.upc_ean, i.region, i.visibility, i.location,
               i.last_watched_on, u.display_name AS owner, (i.owner_id = ?) AS mine
        FROM items i
        JOIN titles t ON t.id = i.title_id
        JOIN users  u ON u.id = i.owner_id
        WHERE (i.visibility = 'shared' OR i.owner_id = ?)
    """
    params = [viewer_id, viewer_id]
    if query:
        sql += " AND (t.name LIKE ? OR COALESCE(i.upc_ean, '') = ?)"
        params += ["%" + query + "%", query]
    if kind:
        sql += " AND t.kind = ?"
        params.append(kind)
    if owner:
        sql += " AND i.owner_id = ?"
        params.append(int(owner))
    wanted = [f for f in (format or "").split(",") if f]
    discs = [f for f in wanted if f != "rip"]
    any_of = []
    if "rip" in wanted:                      # a copy with a file behind it
        any_of.append("EXISTS (SELECT 1 FROM digital_sources r"
                      " WHERE r.item_id = i.id AND r.provider = 'rip')")
    if discs:
        any_of.append("i.format IN (%s)" % ", ".join("?" * len(discs)))
        params += discs
    if any_of:
        sql += " AND (" + " OR ".join(any_of) + ")"
    # a leading "The"/"A"/"An" moves to the end of the key (0025_title_sort_name),
    # so "The A-Team" files under A, the way artists.sort_name already did
    sql += (" ORDER BY COALESCE(NULLIF(t.sort_name, ''), t.name) COLLATE NATSORT,"
            " t.name COLLATE NATSORT, i.format")
    return con.execute(sql, params).fetchall()



def visible_clause(query="", kind="", owner=None, format="",
                   only=False):
    where = ["(i.visibility = 'shared' OR i.owner_id = ?)"]
    params = []                              # viewer id is supplied by the caller
    if query:
        where.append("(t.name LIKE ? OR COALESCE(i.upc_ean, '') = ?"
                     " OR EXISTS (SELECT 1 FROM artists ar"
                     "             WHERE ar.id = t.artist_id"
                     "               AND ar.name LIKE ?)"
                     # a film answers to its series' name too: a collection
                     # with one film in it gets no card of its own, and has
                     # to be reachable by the series name all the same
                     " OR EXISTS (SELECT 1 FROM collections co"
                     "             WHERE co.id = t.collection_id"
                     "               AND co.name LIKE ?))")
        params += ["%" + query + "%", query, "%" + query + "%",
                   "%" + query + "%"]
    if kind:
        where.append("t.kind = ?")
        params.append(kind)
    if owner:
        where.append("i.owner_id = ?")
        params.append(int(owner))
    if True:  # an owner filter must not switch the format filter off
        wanted = [f for f in (format or "").split(",") if f]
        discs = [f for f in wanted if f != "rip"]
        any_of = []
        if "rip" in wanted:                  # a copy with a file behind it
            any_of.append("EXISTS (SELECT 1 FROM digital_sources r"
                          " WHERE r.item_id = i.id AND r.provider = 'rip')")
        if discs:
            any_of.append("i.format IN (%s)" % ", ".join("?" * len(discs)))
            params += discs
        if any_of:
            where.append("(" + " OR ".join(any_of) + ")")
        if only and any_of:
            # only the titles whose every copy is one of the picked formats
            keep = list(discs)
            if "rip" in wanted:
                keep.append("ripped")
            where.append(
                "NOT EXISTS (SELECT 1 FROM items ox WHERE ox.title_id = t.id"
                " AND ox.format NOT IN (%s))"
                % ", ".join("?" * len(keep)))
            params += keep
    return " AND ".join(where), params


def library_stats(con, viewer_id, query="", kind="", owner=None,
                  format="", only=False):
    clause, extra = visible_clause(query, kind, owner, format, only)
    params = [viewer_id] + extra
    base = ("FROM items i JOIN titles t ON t.id = i.title_id WHERE " + clause)
    # A file is spelled format='ripped' whatever it came from - a Jellyfin route
    # (no rip_path) or a rip on disk (rip_path set) - and it is the same copy as
    # its disc, not a second one. It must not pad the tally, which would then
    # disagree with the format pills below (they leave "ripped" out). Titles
    # still come from `base`, so a title holding only a route is listed.
    counted = base + " AND (i.format IS NULL OR i.format <> 'ripped')"
    titles = con.execute("SELECT COUNT(DISTINCT t.id) " + base, params).fetchone()[0]
    copies = con.execute("SELECT COUNT(*) " + counted, params).fetchone()[0]
    # one entry per stored format: a file is "ripped" whatever disc it came from
    # One row per stored format, counted **without** the format filter: the
    # pill row must keep every format, or picking "CD" would leave "CD" as the
    # only pill on the page. The kind and owner chips are counted the same way,
    # each leaving its own dimension out of the tally.
    #
    # A row counts **titles**, like every other chip on the page and like the
    # headline's first number - a pill reading 200 beside "Movies 175" and
    # "Everyone 175" for the same 175 films was counting copies, which is
    # neither the unit of the cards below it nor of any chip beside it. The
    # copies are still counted, for the pill's tooltip (one title can hold a
    # 4K and a Blu-ray, so the copies are not the titles).
    loose, loose_extra = visible_clause(query, kind, owner, "", only)
    formats = con.execute(
        "SELECT i.format AS fmt, COUNT(DISTINCT i.title_id) AS n,"
        "       COUNT(*) AS copies FROM items i"
        " JOIN titles t ON t.id = i.title_id WHERE " + loose +
        " AND (i.format IS NULL OR i.format <> 'ripped')"
        " GROUP BY fmt ORDER BY n DESC",
        [viewer_id] + loose_extra).fetchall()
    return titles, copies, formats

def owner_totals(con):
    return con.execute(
        "SELECT owner, item_count FROM v_owner_totals "
        "WHERE item_count > 0 ORDER BY owner COLLATE NOCASE"
    ).fetchall()


def base_path(handler):
    prefix = (handler.headers.get("X-Ingress-Path") or "").rstrip("/")
    return (prefix + "/") if prefix else "/"


# ------------------------------------------------------------------ pages ----

PAGE = Template("""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<base href="$base">
<title>Media Collection</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 15px/1.45 -apple-system, "Segoe UI", Roboto, sans-serif;
         margin: 0; padding: 1.2rem; background: Canvas; color: CanvasText; }
  h1 { font-size: 1.3rem; margin: 0 0 .2rem; }
  h1 .admin { font-size: .72rem; font-weight: 600; letter-spacing: .04em;
              margin-left: .6rem; opacity: .6; text-transform: uppercase; }
  h2 { font-size: 1rem; margin: 1.6rem 0 .6rem; }
  .sub { opacity: .7; margin: 0 0 1rem; }
  table { border-collapse: collapse; width: 100%; }
  th, td { text-align: left; padding: .35rem .5rem; border-bottom: 1px solid #8883; }
  th { font-size: .78rem; text-transform: uppercase; letter-spacing: .03em; opacity: .65; }
  .tag { font-size: .72rem; border: 1px solid #8886; border-radius: 999px;
         padding: 0 .45rem; white-space: nowrap; }
  .barcode { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .82rem; }
  form.add { display: grid; gap: .8rem; max-width: 46rem; }
  form.add fieldset { border: 1px solid #8883; border-radius: 8px;
                      padding: .3rem .8rem .9rem; margin: 0; min-width: 0; }
  form.add legend { font-size: .78rem; text-transform: uppercase;
                    letter-spacing: .04em; opacity: .7; padding: 0 .3rem; }
  form.add .grid { display: grid; gap: .6rem .9rem;
                   grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr)); }
  form.add .grid label { display: grid; gap: .25rem; font-size: .85rem; }
  form.add .grid .wide { grid-column: 1 / -1; }
  form.add .actions { display: flex; align-items: center; gap: .6rem;
                      margin: 0; }
  form.add .actions .check { margin-right: auto; }
  .withbutton { display: flex; gap: .3rem; align-items: stretch; }
  .withbutton input { flex: 1; min-width: 0; }
  .withbutton button { padding: .3rem .55rem; white-space: nowrap; }
  #upc-said { margin: .4rem 0 0; font-size: .82rem; }
  #scan-video { width: 100%; max-height: 60vh; border-radius: 8px;
                background: #000; }

  [hidden] { display: none !important; }
  input, select, button { font: inherit; padding: .45rem .55rem; border-radius: 6px;
                          border: 1px solid #8886; background: Canvas; color: CanvasText; }
  button { cursor: pointer; font-weight: 600; }
  td button { padding: .15rem .5rem; font-size: .8rem; }
  form.search { display: flex; gap: .5rem; margin: 0 0 .8rem; max-width: 32rem; }
  form.search input { flex: 1; }
  .cards { display: grid; gap: .8rem; grid-template-columns: repeat(auto-fill, minmax(9.5rem, 1fr)); }
  .card { border: 1px solid #8883; border-radius: 8px; overflow: hidden; color: inherit;
          text-decoration: none; display: flex; flex-direction: column; }
  .card .thumb { aspect-ratio: 2 / 3; background: #8882; display: flex; align-items: center;
                 justify-content: center; font-size: .8rem; }
  .card .thumb .muted { opacity: .6; }
  .card .thumb img { width: 100%; height: 100%; object-fit: cover; }
  /* virtual cases: the artwork sits inside a drawn box, so a square cover never
     gets cropped into a poster and a Blu-ray looks like a Blu-ray */
  .thumb { position: relative; }
  /* a collection is several cases in one box: the cover sits in front and two
     more peek out behind it, so a series is never mistaken for the one film
     the search found. The three layers stay inside the thumb, because .card
     clips its contents. */
  .thumb.stack { padding: 8% 8% 0 0; box-sizing: border-box; background: none; }
  .thumb.stack::before, .thumb.stack::after {
      content: ""; position: absolute; left: 0; top: 0;
      width: 92%; height: 94.67%; border-radius: 5px; z-index: 1;
      border: 1px solid #8887; box-shadow: 0 2px 5px #0004;
      background: linear-gradient(180deg, #41454e66, #9aa0ab55); }
  /* the cases step up and to the right of the cover, which sits at the
     bottom-left with its lower edge flush with a title card artwork */
  .thumb.stack::before { left: 4%; top: 2.7%; z-index: 2; }
  .thumb.stack::after { left: 8%; top: 0; }
  .thumb.stack > img { position: relative; z-index: 2; border-radius: 5px;
      box-shadow: 0 0 0 1px #ffffff22, 0 2px 6px #0007; }
  .thumb.stack > .muted { position: relative; z-index: 2; }
  .thumb.case-music { aspect-ratio: 1 / 1; }
  /* Blu-ray, 3D and 4K cases: coloured plastic with a printed band across the top,
     the way the real boxes look (blue / blue with 3D / black with 4K Ultra HD) */
  .thumb.case-blu, .thumb.case-3d, .thumb.case-4k, .thumb.case-dvd {
      aspect-ratio: auto; border-radius: 5px; overflow: hidden;
      display: grid; grid-template-rows: auto auto; align-items: stretch;
      box-shadow: inset 0 0 0 1px #ffffff2e, 0 6px 14px #0006; }
  .thumb .band {
      display: flex; align-items: center; justify-content: center;
      padding: .2rem .25rem; white-space: nowrap;
      font-size: .55rem; font-weight: 700; letter-spacing: .06em; color: #fff;
      text-shadow: 0 1px 2px #0006;
      border-bottom: 1px solid #ffffff33;
      background: linear-gradient(180deg, #ffffff26, #ffffff0a); }
  .thumb.case-blu > .band { font-style: italic; }
  .thumb.case-3d > .band { letter-spacing: .1em; }
  .thumb.case-dvd > .band { letter-spacing: .2em; }
  .thumb.case-blu { background: linear-gradient(180deg, #4fa6e3, #1e6cb8 32%, #0a3d7e); }
  .thumb.case-3d { background: linear-gradient(180deg, #4fa6e3, #1e6cb8 32%, #0a3566); }
  .thumb.case-4k { background: linear-gradient(180deg, #2a2a2a, #08080a 55%, #000); }
  .thumb.case-dvd { background: linear-gradient(180deg, #23252b, #0c0d10 60%, #050506); }
  .thumb.case-blu img, .thumb.case-3d img,
  .thumb.case-4k img, .thumb.case-dvd img {
      /* the band sits on top, so the cover keeps its full 2:3 and nothing is cropped */
      width: 100%; height: auto; aspect-ratio: 2 / 3; object-fit: cover;
      border-radius: 0; box-shadow: none; }
  /* a CD sits in a clear jewel case: hinge on the left, inlay under the plastic */
  .thumb.case-jewel { aspect-ratio: 1 / 1; padding: 0 0 0 12%; border-radius: 3px;
      overflow: hidden;
      background: linear-gradient(100deg, #d7e0e6 0 12%, #f2f7fa 12% 100%);
      box-shadow: inset 0 0 0 1px #ffffff, 0 6px 14px #0005; }
  .thumb.case-jewel::before { content: ""; position: absolute; left: 12%; top: 0;
      bottom: 0; width: 1px; background: #ffffffcc; }
  .thumb.case-jewel img { border: 0; box-shadow: none; }
  /* a vinyl sleeve is the artwork, edge to edge - no frame around it */
  .thumb.case-sleeve { aspect-ratio: 1 / 1; border-radius: 3px;
                       overflow: hidden; padding: 0; background: none; }
  .thumb.case-sleeve img { box-shadow: none; }
  .thumb.case-digital { aspect-ratio: auto; border-radius: 5px; overflow: hidden;
                        display: grid; grid-template-rows: auto auto; }
  .thumb.case-digital img { width: 100%; height: auto; aspect-ratio: 2 / 3;
                            object-fit: cover; }
  .thumb .band.plain { background: #fff; color: #111; border-bottom-color: #ccc; }
  .thumb.case-digital { background: #8881; }
  .card .cap { padding: .4rem .5rem; font-size: .82rem; display: grid; gap: .15rem; }
  .card .cap .muted { font-size: .74rem; }
  .card .cap .playmark { display: block; margin-top: .2rem; font-size: .78rem; }
  label.check { display: flex; align-items: center; gap: .4rem; font-size: .85rem; }
  label.check input { width: auto; }
  nav.modes { display: flex; gap: .4rem; margin: 0 0 .7rem; flex-wrap: wrap; }
  nav.modes a { padding: .38rem .85rem; border-radius: 999px; border: 1px solid #8886;
                color: inherit; text-decoration: none; font-weight: 600;
                font-size: .9rem; }
  nav.modes a.current { background: #2f6f4f; border-color: #2f6f4f; color: #fff; }
  nav.modes .n { opacity: .7; font-weight: 400; margin-left: .3rem; font-size: .82rem; }
  nav.formats, nav.owners { margin-top: -.35rem; }
  nav.formats a, nav.owners a { font-size: .8rem; padding: .22rem .6rem;
                                font-weight: 500; }
  nav.owners .n { font-size: .76rem; }
  .banner { padding: .5rem .7rem; border-radius: 6px; background: #c33;
            color: #fff; margin-bottom: 1rem; }
  .empty { opacity: .6; }
  dialog { border: 1px solid #8886; border-radius: 10px; padding: 1rem 1.2rem;
           max-width: 32rem; width: calc(100% - 2rem);
           max-height: calc(100vh - 2rem); max-height: calc(100dvh - 2rem);
           overflow: auto; overscroll-behavior: contain;
           padding-bottom: calc(1rem + env(safe-area-inset-bottom, 0px)); }
  dialog .actions { position: sticky; bottom: 0; background: Canvas;
           padding-top: .5rem;
           padding-bottom: calc(.5rem + env(safe-area-inset-bottom, 0px)); }
  dialog::backdrop { background: #0008; }
  dialog h2 { margin-top: 0; }
</style>
</head>
<body>
<h1>Media Collection<a class="admin" href="${base}admin">admin</a></h1>
<p class="sub"><button type="button" onclick="addcopy.showModal()">+ Add a title</button></p>
<form class="search" method="get" action="$base">
  <input name="q" value="$q" placeholder="Search title or barcode">
  <input type="hidden" name="kind" value="$kind">
  <input type="hidden" name="owner" value="$owner">
  <input type="hidden" name="view" value="$view">
  $format_hidden
  <button type="submit">Search</button>
</form>
$modes
<nav class="modes formats">$format_tabs</nav>
<nav class="modes owners">$owner_tabs</nav>
<p class="sub">$viewtoggle$clear</p>
$notice
$error
<h2>$heading$scope</h2>
$library

<dialog id="addcopy">
<h2>Add a title</h2>
<form class="add" method="post" action="${base}items"
      onsubmit="adding(this)">
  <fieldset>
    <legend>The title</legend>
    <div class="grid">
      <label>Media type
        <select name="kind" onchange="addKind(this.value)">
          <option value="movie">Film</option>
          <option value="tv">TV</option>
          <option value="music">Music</option>
        </select>
      </label>
      <label>Title<input name="title" placeholder="The Lion King" required></label>
      <label>Year<input name="release_year" placeholder="1994"
        inputmode="numeric" pattern="[0-9]{0,4}"></label>
      <label class="screen">IMDb id<input name="imdb_id"
        placeholder="tt0110357"></label>
      <label class="music" hidden>Artist<input name="artist"
        placeholder="The Beatles"></label>
      <label class="music" hidden>Artist MusicBrainz id
        <input name="artist_mbid" placeholder="optional"></label>
      <label class="music" hidden>Release MusicBrainz id
        <input name="musicbrainz_id" placeholder="optional"></label>
    </div>
  </fieldset>
  <fieldset>
    <legend>The copy</legend>
    <div class="grid">
      <label>Format
        <select name="format" onchange="addFormat(this.value)">
$format_options
        </select>
      </label>
      <label>Owner<select name="owner_id">$owner_options</select></label>
      <label>Store$store_options</label>
      <label>Visibility
        <select name="visibility">
          <option value="shared">Shared with family</option>
          <option value="private">Private to me</option>
        </select>
      </label>
      <label>Edition<input name="edition"
        placeholder="Director's cut, remaster…"></label>
      <label class="wide">Barcode
        <span class="withbutton">
          <input name="upc_ean" placeholder="UPC / EAN" inputmode="numeric"
            pattern="[0-9]{12,13}" title="12 or 13 digits">
          <button type="button" onclick="startScan()">Scan</button>
          <button type="button" onclick="lookUp()">Look up</button>
        </span>
      </label>
      <label>Where it lives<input name="location"
        placeholder="Lounge shelf"></label>
    </div>
    <p class="sub" id="upc-said" hidden></p>
  </fieldset>
  <p class="sub" id="add-status" hidden>Adding the title and looking up its
    metadata - this can take a few seconds.</p>
  <p class="actions">
    <label class="check"><input type="checkbox" name="fetch_metadata" value="1"
      checked>
      Look up metadata</label>
    <button type="button" onclick="addcopy.close()">Cancel</button>
    <button type="submit">Add title</button>
  </p>
</form>
</dialog>

<dialog id="scanner">
<h2>Scan a barcode</h2>
<video id="scan-video" playsinline muted></video>
<p class="sub" id="scan-said">Point the camera at the barcode on the case.
  Camera access needs a secure page: open the catalogue over https (or on
  localhost). Inside Home Assistant the panel is framed, and the browser
  withholds the camera from a framed page.</p>
<p class="actions">
  <button type="button" onclick="stopScan()">Cancel</button>
</p>
</dialog>

<script>
// The title dialog shows only what the chosen media type needs: IMDb for
// film and TV, the MusicBrainz trio for music. `hidden` rather than a class,
// so an unticked field is never submitted empty-handed by the browser's
// own validation either.
// A new title is inserted and then looked up (Wikipedia, Wikidata, or
// MusicBrainz), which takes seconds while the page sits there answering
// nothing - so say so and stop the button taking a second click. The disable
// is deferred: a submit handler that disables its own button can cancel the
// submission in some browsers.
function adding(form) {
  var note = document.getElementById('add-status');
  if (note) { note.hidden = false; }
  var button = form.querySelector('button[type="submit"]');
  if (!button) { return; }
  button.textContent = 'Adding...';
  setTimeout(function () { button.disabled = true; }, 0);
}
function addKind(kind) {
  var dlg = document.getElementById('addcopy');
  if (!dlg) { return; }
  var music = kind === 'music';
  var i;
  var musicFields = dlg.querySelectorAll('.music');
  for (i = 0; i < musicFields.length; i++) { musicFields[i].hidden = !music; }
  var screenFields = dlg.querySelectorAll('.screen');
  for (i = 0; i < screenFields.length; i++) { screenFields[i].hidden = music; }
}
// a record or a tape is music whatever the media type said a moment ago
function addFormat(format) {
  var musicFormat = ['cd', 'vinyl', 'sacd', 'cassette'].indexOf(format) >= 0;
  var dlg = document.getElementById('addcopy');
  if (!dlg || !musicFormat) { return; }
  var pick = dlg.querySelector('select[name="kind"]');
  if (pick && pick.value !== 'music') {
    pick.value = 'music';
    addKind('music');
  }
}
document.addEventListener('DOMContentLoaded', function () {
  var dlg = document.getElementById('addcopy');
  if (!dlg) { return; }
  var pick = dlg.querySelector('select[name="kind"]');
  if (pick) { addKind(pick.value); }
});

// ------------------------------------------------------------- barcodes
// Scan the case with the camera (BarcodeDetector, where the browser has
// one) or type the digits, then ask the catalogue what they belong to.
var scanTimer = null;
var scanStream = null;

function scanSupported() {
  return typeof window.BarcodeDetector === 'function' &&
         !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
}

function stopScan() {
  if (scanTimer) { clearInterval(scanTimer); scanTimer = null; }
  if (scanStream) {
    scanStream.getTracks().forEach(function (track) { track.stop(); });
    scanStream = null;
  }
  var video = document.getElementById('scan-video');
  if (video) { video.srcObject = null; }
  var dlg = document.getElementById('scanner');
  if (dlg && dlg.open) { dlg.close(); }
}

function barcodeField() {
  var dlg = document.getElementById('addcopy');
  return dlg ? dlg.querySelector('input[name="upc_ean"]') : null;
}

function webSearch(code) {
  var pick = document.querySelector('select[name="format"]');
  var word = '';
  if (pick) {
    if (pick.value === 'dvd') { word = ' dvd'; }
    else if (/^(bluray|bluray_3d|uhd_4k|vhs)$$/.test(pick.value)) {
      word = pick.value === 'vhs' ? ' vhs' : ' blu-ray';
    }
  }
  return 'https://www.google.com/search?q=' +
    encodeURIComponent((code || '') + word);
}

function say(text, href) {
  var said = document.getElementById('upc-said');
  if (!said) { return; }
  said.hidden = !text;
  said.textContent = text || '';
  if (!href) { return; }
  var link = document.createElement('a');
  link.href = href;
  link.target = '_blank';
  link.rel = 'noopener';
  link.textContent = 'Search the web';
  said.appendChild(document.createTextNode(' '));
  said.appendChild(link);
}

function startScan() {
  var dlg = document.getElementById('scanner');
  var said = document.getElementById('scan-said');
  var video = document.getElementById('scan-video');
  if (!dlg || !said || !video) { return; }
  if (!scanSupported()) {
    said.textContent = window.isSecureContext === false
      ? 'The camera is only offered to a secure page. Open the catalogue over '
        + 'https - the add-on serves it directly on port 8099 once a '
        + 'certificate is in place - then press Scan again.'
      : 'This browser cannot read barcodes. Type the digits, '
        + 'or open the page in Chrome on a phone.';
    dlg.showModal();
    return;
  }
  said.textContent = 'Starting the camera...';
  dlg.showModal();
  navigator.mediaDevices.getUserMedia({video: {facingMode: 'environment'}})
    .then(function (stream) {
      scanStream = stream;
      video.srcObject = stream;
      var play = video.play();
      if (play && play.catch) { play.catch(function () {}); }
      said.textContent = 'Point the camera at the barcode on the case.';
      var detector;
      try {
        detector = new window.BarcodeDetector(
          {formats: ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128']});
      } catch (err) {
        detector = new window.BarcodeDetector();
      }
      scanTimer = setInterval(function () {
        if (video.readyState < 2) { return; }        // no frame yet
        detector.detect(video).then(function (codes) {
          if (!scanTimer) { return; }            // cancelled meanwhile
          if (!codes || !codes.length) { return; }
          var value = String(codes[0].rawValue || '').replace(/[^0-9]/g, '');
          if (value.length < 12) { return; }
          var field = barcodeField();
          stopScan();
          if (field) { field.value = value; }
          lookUp();
        }).catch(function () {});
      }, 400);
    })
    .catch(function (err) {
      said.textContent = 'No camera: ' + (err.message || err.name);
    });
}

function lookUp() {
  var field = barcodeField();
  if (!field) { return; }
  var code = field.value.replace(/[^0-9]/g, '');
  if (code.length !== 12 && code.length !== 13) {
    say('A barcode is 12 or 13 digits.');
    return;
  }
  field.value = code;
  say('Looking up ' + code + '...');
  fetch('barcode?upc=' + code, {headers: {'Accept': 'application/json'}})
    .then(function (reply) { return reply.json(); })
    .then(fillFromBarcode)
    .catch(function () {
      say('Could not look that barcode up - is the catalogue offline?');
    });
}

function fillFromBarcode(data) {
  var dlg = document.getElementById('addcopy');
  if (!dlg) { return; }
  var notes = [];
  if (data.existing) {
    notes.push('Already in the catalogue: ' + data.existing.name
      + ' (#' + data.existing.id + ').');
  }
  var found = data.found;
  if (!found) {
    notes.push('No match for ' + (data.upc || 'that barcode') + '.');
    if (data.note) { notes.push(data.note); }
    notes.push('Type the title from the case, or search the web for the code.');
    say(notes.join(' '), webSearch(data.upc));
    return;
  } else {
    var fill = function (name, value) {
      var field = dlg.querySelector('[name="' + name + '"]');
      if (field && value && !String(field.value).trim()) {
        field.value = value;
      }
    };
    fill('title', found.title);
    fill('release_year', found.year ? String(found.year) : '');
    if (found.kind === 'music') {
      var pick = dlg.querySelector('select[name="kind"]');
      if (pick && pick.value !== 'music') {
        pick.value = 'music';
        addKind('music');
      }
      fill('artist', found.artist);
      fill('musicbrainz_id', found.musicbrainz_id);
    }
    notes.push(found.source + ': ' + found.title
      + (found.artist ? ' - ' + found.artist : '')
      + (found.year ? ' (' + found.year + ')' : ''));
  }
  if (data.note) { notes.push(data.note); }
  say(notes.join(' '));
}
</script>
$nowplaying
</body>
</html>
""")

ROW = Template("""<tr>
  <td><a href="${base}title/$title_id$from_query">$title</a></td>
  <td>$year</td>
  <td><span class="tag">$format</span></td>
  <td>$owner$private</td>
  <td class="barcode">$barcode</td>
  <td>$watched</td>
  <td>$location</td>
  <td>$delete</td>
</tr>""")


def _span(row):
    """'2012-2025' from a first_year/last_year pair, when a row carries one."""
    first = row["first_year"] if "first_year" in row.keys() else None
    last = row["last_year"] if "last_year" in row.keys() else None
    if first and last and first != last:
        return "%d\u2013%d" % (first, last)
    return str(first or last or "")


def _place_cover(base, row):
    """The picture for a card that stands for a page rather than a copy."""
    if "jf_id" in row.keys() and row["jf_id"]:
        return base + "art/" + row["jf_id"]
    poster = (row["poster_url"] if "poster_url" in row.keys() else "") or ""
    if not poster and "member_poster" in row.keys():
        poster = row["member_poster"] or ""
    return ui.cover_url(base, poster)


def _place_card(base, href, cover, name, caption, stack=False):
    """A card shaped like a title's, whose link opens an act or a series.

    `stack=True` draws the thumbnail as a stack of cases - the cover in front,
    two more peeking out behind - because a collection is several films in one
    box and would otherwise look like the single film that was searched for.
    """
    thumb = ('<img src="%s" alt="" loading="lazy">' % html.escape(cover)
             if cover else '<span class="muted">no image</span>')
    return ('<a class="card" href="%s%s"><div class="thumb%s">%s</div>'
            '<div class="cap"><strong>%s</strong>'
            '<span class="muted">%s</span></div></a>'
            % (html.escape(base), href, " stack" if stack else "", thumb,
               html.escape(name), html.escape(caption)))


def render_index(con, viewer, base, error=None, query="",
                 view="list", kind="", owner=None, format="", only=False):
    sorting.register(con)      # the card query orders by NATSORT too
    bits = []
    if query:
        bits.append("q=" + quote(query))
    if kind:
        bits.append("kind=" + quote(kind))
    if owner:
        bits.append("owner=%d" % owner)
    if view and view != "list":
        bits.append("view=" + quote(view))
    if format:
        bits.append("format=" + quote(format))
    here = "?" + "&".join(bits) if bits else ""
    # what a title page needs to come back here. It is quoted: unquoted,
    # the first "&" ends the ?from= value and every filter after it is
    # lost when the title page rebuilds the link (owner was the visible
    # casualty - the catalogue kept the owner, the Back link did not).
    back_suffix = f"?from={quote(here, safe='')}" if here else ""
    rows = library_rows(con, viewer["id"], query, kind, owner, format)
    titles, copies, formats = library_stats(con, viewer["id"], query,
                                            kind, owner, format)
    heading = "%d title%s · %d cop%s" % (titles, "" if titles == 1 else "s", copies,
                                        "y" if copies == 1 else "ies")
    def tally_link(fmt):
        bits = []
        if query:
            bits.append("q=" + quote(query))
        if kind:
            bits.append("kind=" + quote(kind))
        if owner:
            bits.append("owner=%d" % owner)
        if view and view != "list":
            bits.append("view=" + quote(view))
        if fmt:
            bits.append("format=" + quote(fmt))
        return base + ("?" + "&".join(bits) if bits else "")

    picked = [f for f in (format or "").split(",") if f]

    def hidden(name, value):
        return '<input type="hidden" name="%s" value="%s">' % (
            name, html.escape(str(value)))

    def format_link(fmt):
        """This page with `fmt` added to, or taken out of, the format filter.

        The pills apply on the click, like the kind and owner chips: a format
        is a filter, not a form to fill in and then post.
        """
        chosen = [f for f in picked if f != fmt]
        if fmt not in picked:
            chosen.append(fmt)
        return tally_link(",".join(chosen))

    format_tabs = "".join(
        '<a class="%s" href="%s" title="%s">%s<span class="n">%d</span></a>'
        % ("current" if row["fmt"] in picked else "",
           html.escape(format_link(row["fmt"])),
           html.escape("%d title%s, %d cop%s"
                       % (row["n"], "" if row["n"] == 1 else "s",
                          row["copies"], "y" if row["copies"] == 1
                          else "ies")),
           html.escape(FORMAT_LABELS.get(row["fmt"], row["fmt"])),
           row["n"])
        for row in formats if row["fmt"] != "ripped")
    # Every row of pills leaves its own dimension out of its tally, so the
    # numbers agree with one another: the formats (above) count the search,
    # the kind and the owner; the kind chips count the search, the owner and
    # the formats; the owners count the search, the kind and the formats. A
    # search that finds four albums must not sit under "Music 560".
    mode_clause, mode_extra = visible_clause(query, "", owner, format, only)
    mode_counts = {row["kind"]: row["n"] for row in con.execute(
        "SELECT t.kind AS kind, COUNT(DISTINCT t.id) AS n FROM items i"
        " JOIN titles t ON t.id = i.title_id"
        " WHERE " + mode_clause +
        " GROUP BY t.kind", [viewer["id"]] + mode_extra)}
    extra = "".join([   # the filters a kind or owner chip has to keep
        "&amp;q=" + quote(query) if query else "",
        "&amp;format=" + quote(format) if format else "",
        "&amp;view=grid" if view == "grid" else "",
    ])
    # A kind chip's number is what **that view** would hold with the current
    # filters, which is not what a reader takes it for: with 4K and Digital
    # picked, "Music 14" was read as "14 music titles are on this page". So once
    # a filter narrows the shelf only the media type you are looking at carries
    # a count; the others stay plain links, with their number in a tooltip where
    # it cannot be mistaken for a count of the page.
    narrowed = bool(query or picked or owner)

    def mode_chip(value, label):
        count = mode_counts.get(value, 0)
        current = value == kind
        href = "%s?kind=%s%s" % (html.escape(base), value, extra)
        if current or not narrowed:
            return ('<a class="%s" href="%s">%s<span class="n">%d</span></a>'
                    % ("current" if current else "", href, label, count))
        if count:
            tip = "%d title%s in %s match these filters" % (
                count, "" if count == 1 else "s", label)
        else:
            tip = "No titles in %s match these filters" % label
        return ('<a href="%s" title="%s">%s</a>'
                % (href, html.escape(tip), label))

    modes = "".join(
        mode_chip(value, label)
        for value, label in (MODES + (("other", "Other"),)
                             if mode_counts.get("other") else MODES))
    # An empty view must not be a dead end: if the filters match nothing of this
    # media type but do match another, say so here with a link that keeps the
    # filters - the number moved into a tooltip above would otherwise be
    # invisible to a reader who needed it most.
    if not titles:
        elsewhere = [(value, label) for value, label in MODES
                     if value != kind and mode_counts.get(value)]
        if elsewhere:
            modes += ('<p class="sub">Nothing in %s &mdash; %s.</p>'
                      % (html.escape(dict(MODES).get(kind, kind) or kind),
                         " and ".join(
                             "%d in %s" % (mode_counts[value],
                                           '<a href="%s?kind=%s%s">%s</a>'
                                           % (html.escape(base), value, extra,
                                              html.escape(label)))
                             for value, label in elsewhere)))
    # every household member appears, even with nothing in this mode, so a shared
    # library never disappears from the filter
    owner_rows = con.execute(
        "SELECT u.id, u.display_name FROM users u"
        "  WHERE u.username <> 'rips' AND EXISTS ("
        "  SELECT 1 FROM items i WHERE i.owner_id = u.id"
        "  AND (i.visibility = 'shared' OR i.owner_id = ?))"
        " ORDER BY u.display_name COLLATE NOCASE", (viewer["id"],)).fetchall()
    owner_clause, owner_extra = visible_clause(query, kind, None, format, only)
    owner_counts = {row["owner_id"]: row["n"] for row in con.execute(
        "SELECT i.owner_id AS owner_id, COUNT(DISTINCT t.id) AS n FROM items i"
        " JOIN titles t ON t.id = i.title_id"
        " WHERE " + owner_clause +
        " GROUP BY i.owner_id", [viewer["id"]] + owner_extra)}
    # the current kind applies here too, or "Everyone" sits at the all-kinds
    # total while the chips beside it count only this kind
    everyone = con.execute(
        "SELECT COUNT(DISTINCT t.id) FROM items i"
        " JOIN titles t ON t.id = i.title_id"
        " WHERE " + owner_clause,
        [viewer["id"]] + owner_extra).fetchone()[0]
    owner_tabs = "".join(
        ['<a class="%s" href="%s?kind=%s%s">Everyone<span class="n">%d</span></a>'
         % ("" if owner else "current", html.escape(base), kind,
            extra,
            everyone)]
        + ['<a class="%s" href="%s?kind=%s&amp;owner=%d%s">%s<span class="n">%d</span></a>'
           % ("current" if owner == row["id"] else "", html.escape(base), kind,
              row["id"], "&amp;view=grid" if view == "grid" else "",
              html.escape(row["display_name"]), owner_counts.get(row["id"], 0))
           for row in owner_rows])
    notice = ""
    # A search also finds the shelves behind the titles: the act whose name was
    # typed, the series a film belongs to. They come back as cards of their own
    # - title-shaped, but the link opens a page rather than a copy.
    place_cards, place_rows = [], []
    if query:
        places = []
        if kind in ("", "music"):
            for row in artists.matching(con, viewer["id"], query, owner):
                places.append((row, "artist/%d" % row["id"], "Artist",
                               "%d album%s" % (
                                   row["albums"],
                                   "" if row["albums"] == 1 else "s"),
                               _span(row)))
        if kind in ("", "movie", "tv"):
            for row in boxsets.matching(con, viewer["id"], query, owner):
                if kind and (row["kind"] or "") != kind:
                    continue        # a TV series under Movies is noise
                if row["films"] < 2:
                    # one film is not a shelf worth a card of its own - that
                    # film already answers to the collection's name, because
                    # visible_clause matches it too
                    continue
                places.append((row,
                               "collection/%d?q=%s" % (row["id"],
                                                       quote(query)),
                               "Collection",
                               "%d film%s" % (
                                   row["films"],
                                   "" if row["films"] == 1 else "s"),
                               _span(row)))
        for row, href, label, count, span in places:
            place_cards.append(_place_card(
                base, href, _place_cover(base, row), row["name"],
                " \u00b7 ".join(filter(None, [label, count, span])),
                stack=(label == "Collection")))
            place_rows.append(
                '<tr><td><a href="%s%s">%s</a></td><td class="muted">%s</td>'
                '<td><span class="tag">%s</span></td>'
                '<td colspan="5" class="muted">%s</td></tr>'
                % (html.escape(base), href, html.escape(row["name"]),
                   html.escape(span), label, html.escape(count)))
    place_cards = "".join(place_cards)
    place_rows = "".join(place_rows)

    if view == "grid":
        clause, extra = visible_clause(query, kind, owner, format,
                                       only)
        fmt_wanted = [f for f in (format or "").split(",") if f]

        # An owner filter narrows the copies a title is judged by, so the card's
        # format line has to narrow with them: Mitchell's 4K must not be
        # captioned with Matt's digital copy of the same film. The play mark is
        # left title-wide on purpose - a Jellyfin route belongs to the
        # household, not to whichever copy the viewer is looking at.
        fmt_owner = " AND ai.owner_id = ?" if owner else ""
        fmt_owner_params = [int(owner)] if owner else []

        cards = con.execute(
            "SELECT t.id, t.name, t.release_year, t.poster_url, t.kind,"
            " COALESCE(t.art_source, '') AS art_source,"
            " (SELECT l.jf_id FROM jellyfin_links l"
            "   JOIN items li ON li.id = l.item_id"
            "   WHERE li.title_id = t.id ORDER BY l.jf_kind LIMIT 1) AS jf_id,"
            " (SELECT a.name FROM artists a WHERE a.id = t.artist_id)"
            "   AS artist_name,"
            " COUNT(i.id) AS copies,"
            # a file behind this title, so the card can offer to play it
            " (SELECT 1 FROM items ri JOIN digital_sources rd"
            "   ON rd.item_id = ri.id"
            "   WHERE ri.title_id = t.id AND rd.provider IN ('rip', 'jellyfin')"
            "   LIMIT 1) AS has_rip,"

            # the formats these copies hold - fmt_owner narrows it to the owner
            " (SELECT group_concat(DISTINCT ai.format) FROM items ai"
            "   WHERE ai.title_id = t.id"
            "     AND (ai.visibility = 'shared' OR ai.owner_id = ?)"
            + fmt_owner + ") AS formats,"
            " (SELECT group_concat(DISTINCT st.label) FROM items si"
            "    JOIN digital_sources sd ON sd.item_id = si.id"
            "    JOIN stores st ON st.key = sd.provider"
            "   WHERE si.title_id = t.id AND si.format = 'digital'"
            "     AND st.kind <> 'system') AS shops"
            " FROM titles t JOIN items i ON i.title_id = t.id"
            " WHERE " + clause +
            " GROUP BY t.id ORDER BY COALESCE(NULLIF(t.sort_name, ''), t.name)"
            " COLLATE NATSORT, t.name COLLATE NATSORT",
            [viewer["id"]] + fmt_owner_params + [viewer["id"]] + extra).fetchall()
        library = '<div class="cards">%s</div>' % (place_cards + "".join(
            '<a class="card" href="{0}title/{1}{5}">{2}'
            '<div class="cap"><strong>{3}</strong><span class="muted">{4}</span></div></a>'
            .format(
                base, card["id"],
                '<div class="thumb %s">%s%s</div>' % (
                    case_class(card["kind"], shown_formats(card["formats"], picked)),
                    case_band(card["kind"], shown_formats(card["formats"], picked),
                              card["shops"]),
                    '<img src="%s" alt="" loading="lazy">' % html.escape(
                        ui.pick_cover(base, card["jf_id"], card["poster_url"],
                                      card["art_source"]))
                    if (card["jf_id"] or card["poster_url"])
                    else '<span class="muted">no image</span>'),
                html.escape(card["name"])
                    + (' <span class="playmark" title="a copy is ready to play">&#9654;</span>'
                       if card["has_rip"] else ""),
                html.escape(" · ".join(filter(None, [
                    str(card["release_year"] or ""),
                    card["artist_name"] or "",
                    " · ".join(FORMAT_LABELS.get(f, f)
                               for f in quality_sorted(",".join(f for f in (card["formats"] or "").split(",")
                                   if f != "ripped"
                                   and (not fmt_wanted or f in fmt_wanted)))),
                    ])))
                , back_suffix
            )
            for card in cards) or ("" if place_cards else '<p class="empty">Nothing yet — add your first disc.</p>'))
    else:
        body = "".join(
            ROW.substitute(
                base=base,
                title_id=r["title_id"],
                from_query=back_suffix,
                title=html.escape(r["title"]),
                year=r["release_year"] or "",
                format=FORMAT_LABELS.get(r["format"], r["format"]),
                owner=html.escape(r["owner"]),
                private="" if r["visibility"] == "shared" else ' <span class="tag">private</span>',
                barcode=html.escape(r["upc_ean"] or "—"),
                watched=html.escape(r["last_watched_on"] or ""),
                location=html.escape(r["location"] or ""),
                delete=(
                    '<form method="post" action="{0}items/{1}/delete">'
                    '<button type="submit">Remove</button></form>'.format(base, r["id"])
                    if r["mine"] else ""
                ),
            )
            for r in rows
        ) or ("" if place_rows else '<tr><td colspan="8" class="empty">Nothing yet — add your first disc.</td></tr>')
        library = ("<table><thead><tr><th>Title</th><th>Year</th><th>Format</th>"
                   "<th>Owner</th><th>Barcode</th><th>Watched</th><th>Where</th>"
                   "<th></th></tr></thead><tbody>%s</tbody></table>"
                   % (place_rows + body))
    return PAGE.substitute(
        base=base,
        nowplaying=ui.NOW_PLAYING,
        viewer=html.escape(viewer["display_name"]),
        error='<p class="banner">%s</p>' % html.escape(error) if error else "",
        notice=notice,
        count=len(rows),
        heading=heading,
        format_tabs=format_tabs,
        format_hidden="".join(hidden("format", f) for f in picked),
        kind=kind,
        owner=owner or "",
        modes="<nav class=\"modes\">%s</nav>" % modes,
        owner_tabs=owner_tabs,
        scope=' matching “%s”' % html.escape(query) if query else "",
        q=html.escape(query),
        clear=(' · <a href="%s?kind=%s%s">clear search</a>'
               % (html.escape(base), kind,
                  "&amp;owner=%d" % owner if owner else "") if query else ""),
        view=view,
        viewtoggle=(' · <a href="%s?view=%s&amp;kind=%s%s%s%s">%s</a>'
                    % (html.escape(base), "list" if view == "grid" else "grid",
                       html.escape(kind),
                       "&amp;q=" + html.escape(query) if query else "",
                       "&amp;owner=%d" % owner if owner else "",
                       (("&amp;format=" + html.escape(format)) if format else ""),
                       "List view" if view == "grid" else "Thumbnails")
                    + (' · <a href="%sartists">Artists</a>'
                       % html.escape(base) if kind == "music" else "")
                    + (' · <a href="%scollections">Collections</a>'
                       % html.escape(base)
                       if kind in ("", "movie", "tv") else "")),
        library=library,
        format_options="".join(
            '<option value="{0}">{1}</option>'.format(f, FORMAT_LABELS[f]) for f in FORMATS
        ),
        owner_options="".join(
            '<option value="{0}"{2}>{1}</option>'.format(
                row["id"], html.escape(row["display_name"]),
                " selected" if row["id"] == viewer["id"] else "")
            for row in owner_rows),
        # no store is the default: a disc was not bought from an online
        # shop, so adding one must not record a purchase it never had. A
        # datalist lets a shop nobody has used yet be typed in - the save
        # turns it into a row, so it is on the list from then on.
        store_options=('<input name="store" list="store-choices" value="" '
                       'placeholder="-"><datalist id="store-choices">%s'
                       '</datalist>'
                       % "".join('<option value="%s"></option>' % html.escape(name)
                                 for _key, name in stores.choices(con))),
    )


# ----------------------------------------------------------------- routes ----

def route_path(handler):
    """Path with the ingress prefix stripped, so routing is prefix-agnostic."""
    path = urlparse(handler.path).path
    prefix = (handler.headers.get("X-Ingress-Path") or "").rstrip("/")
    if prefix and path.startswith(prefix):
        path = path[len(prefix):]
    return path if path.startswith("/") else "/" + path


def field(form, name):
    return (form.get(name) or [""])[0].strip()


def image_ext(blob):
    """(.ext, content type) for an uploaded picture, or ("", "") if it is none.

    A browser sends the file name and a content type, and both can be made
    up by any client; the first bytes of the file cannot be.
    """
    if blob.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if blob.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if blob.startswith((b"GIF87a", b"GIF89a")):
        return ".gif", "image/gif"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return "", ""


def _forget_cover_file(url):
    """Delete an uploaded picture from the disk once nothing shows it."""
    url = (url or "").strip()
    if not url.startswith("/cover/"):
        return
    name = os.path.basename(url)
    if not name:
        return
    try:
        os.remove(os.path.join(COVERS_DIR, name))
    except OSError:
        pass


def _title_row(con, title, year):
    """The title a POST is about, matched the way the catalogue files it."""
    return con.execute(
        "SELECT t.id, t.name, t.release_year, t.imdb_id, t.kind,"
        "       a.name AS artist_name"
        "  FROM titles t LEFT JOIN artists a ON a.id = t.artist_id"
        " WHERE lower(t.name) = lower(?)"
        " AND COALESCE(t.release_year, -1) = COALESCE(?, -1)"
        " ORDER BY t.id LIMIT 1", (title, year)).fetchone()


def _near_title(con, title, year, kind):
    """The title this POST would twin, when the year cannot tell them apart.

    `_title_row()` needs the two years to agree, so a second add with the Year
    box left empty never matched the row already on the shelf - and with
    *Look up metadata* ticked the new row then gained the very same year from
    MusicBrainz, leaving two identical titles (*Let the music play*, three
    times on the live shelf).

    Two different films that share a name always carry their own years, so
    only a missing one is ambiguous: a posted year matches a stored NULL or an
    equal year, and a blank posted year matches any row of that name and kind.
    """
    sql = ("SELECT t.id, t.name, t.release_year, t.imdb_id, t.kind,"
           "       a.name AS artist_name"
           "  FROM titles t LEFT JOIN artists a ON a.id = t.artist_id"
           " WHERE lower(TRIM(t.name)) = lower(TRIM(?))"
           "   AND t.kind = ?")
    args = [title, kind]
    if year is not None:
        sql += " AND (t.release_year IS NULL OR t.release_year = ?)"
        args.append(year)
    sql += " ORDER BY t.id LIMIT 1"
    return con.execute(sql, tuple(args)).fetchone()


def _same_act(existing, form):
    """Is a music title the same act's work, not just the same album name?

    Two bands can both have an album called "Greatest Hits", so a name alone
    must not make one of them a duplicate of the other. An empty artist on
    either side counts as "cannot tell", which keeps the block.
    """
    if not existing or existing["kind"] != "music":
        return True
    wants = (field(form, "artist") or "").strip().lower()
    holds = (existing["artist_name"] or "").strip().lower()
    return not (wants and holds and wants != holds)


def _same_copies(con, title_id, owner_id, fmt):
    """The copies a new one would duplicate: same title, owner and format.

    A second copy is a real thing (one for the lounge, one for a bedroom), so
    this only ever asks - it never refuses.
    """
    return con.execute(
        "SELECT i.id, i.format, i.location, i.acquired_on,"
        "       COALESCE(u.display_name, 'somebody') AS who"
        "  FROM items i LEFT JOIN users u ON u.id = i.owner_id"
        " WHERE i.title_id = ? AND i.owner_id = ? AND i.format = ?"
        " ORDER BY i.id", (title_id, owner_id, fmt)).fetchall()


def _hidden_fields(form, skip=()):
    """The submitted form again, as hidden inputs, so it can be re-posted."""
    out = []
    for name, values in form.items():
        if name in skip:
            continue
        for value in values:
            out.append('<input type="hidden" name="%s" value="%s">'
                       % (html.escape(name, quote=True),
                          html.escape(value, quote=True)))
    return "".join(out)


def clash_page(base, form, clash):
    """Nothing was written: stop a duplicate title, or ask about a copy."""
    row = clash["title"]
    name = html.escape(row["name"])
    year = row["release_year"]
    label = f"{name} ({year})" if year else name
    link = f"{html.escape(base)}title/{row['id']}"
    if clash["kind"] == "title":
        return ui.page(base, "Already in the catalogue", f"""
<section class="panel">
<h2>{label} is already here</h2>
<p class="sub">Nothing was added: title #{row['id']} already carries that name
   and year.</p>
<p>If what you have is another copy of it, open the title and press
   <em>Add a copy</em> - that one asks before it makes a duplicate.</p>
<p><a href="{link}?tab=copies"><button type="button">Open {name}</button></a></p>
</section>""")

    first = clash["copies"][0]
    fmt = FORMAT_LABELS.get(clash["format"], clash["format"])
    who = html.escape(first["who"])
    place = f" in {html.escape(first['location'])}" if first["location"] else ""
    when = (f", added {html.escape(str(first['acquired_on'])[:10])}"
            if first["acquired_on"] else "")
    many = len(clash["copies"])
    more = f" You already hold {many} of them." if many > 1 else ""
    return ui.page(base, "Add a copy", f"""
<dialog class="sheet" open>
<h2>Another copy?</h2>
<p>{who} already owns a {fmt} of {label}{place}{when}.{more}</p>
<p class="sub">Nothing has been added yet.</p>
<form method="post" action="{html.escape(base)}items">
  {_hidden_fields(form, ('confirm_copy',))}
  <input type="hidden" name="confirm_copy" value="1">
  <p><button type="submit">Add another copy</button>
     <a href="{link}?tab=copies">Cancel</a></p>
</form>
</dialog>""")


def add_item(con, viewer, form):
    """Insert a title (or reuse the one this copy belongs to) + one copy.

    Returns (error, title_id, clash). error is a message for the user,
    title_id the row that was added to. clash means nothing was written
    and the caller must show the page it describes:

        {"kind": "title", "title": row}  that title is already here
        {"kind": "copy", "title": row, "format": fmt, "copies": [...]}
    """
    title = field(form, "title")
    if not title:
        return "Title is required.", None, None

    kind = field(form, "kind")
    if kind not in KINDS:
        kind = "movie"

    year_raw = field(form, "release_year")
    year = int(year_raw) if year_raw.isdigit() else None

    fmt = field(form, "format") or "bluray"
    if fmt in ("vinyl", "cd"):      # a record or a disc is music, whatever
        kind = "music"             # the form claimed

    if fmt not in FORMATS:
        return "Unknown format.", None, None

    upc = re.sub(r"\D", "", field(form, "upc_ean")) or None
    if upc and len(upc) not in (12, 13):
        return "UPC/EAN must be 12 or 13 digits.", None, None

    visibility = field(form, "visibility") or DEFAULT_VISIBILITY
    if visibility not in ("shared", "private"):
        visibility = "shared"

    imdb = (field(form, "imdb_id") or "").strip() or None
    owner_text = (re.sub(r"\D", "", field(form, "owner_id"))
                  or str(viewer["id"]))
    confirming = bool(field(form, "confirm_copy"))
    existing = _title_row(con, title, year)
    if not existing:
        existing = _near_title(con, title, year, kind)
    if not _same_act(existing, form):
        existing = None            # a different act's album of the same name
    if existing and not field(form, "from") and not confirming:
        # the catalogue dialog only ever starts a title: it must not
        # quietly become a second copy of one that is already here
        return None, None, {"kind": "title", "title": existing}
    if existing and not confirming:
        same = _same_copies(con, existing["id"], int(owner_text), fmt)
        if same:
            return None, None, {"kind": "copy", "title": existing,
                                "format": fmt, "copies": same}
    if existing:
        title_id = existing["id"]
        if imdb and imdb != existing["imdb_id"]:
            con.execute("UPDATE titles SET imdb_id = ?, overview = NULL, poster_url = NULL,"
                        " metadata_source = NULL WHERE id = ?", (imdb, title_id))
    else:
        title_id = con.execute(
            "INSERT INTO titles (name, release_year, created_by, imdb_id, kind)"
            " VALUES (?, ?, ?, ?, ?)",
            (title, year, viewer["id"], imdb, kind)).lastrowid

    if kind == "music":
        # an album can be filed under its artist and MusicBrainz release
        release_mbid = (field(form, "musicbrainz_id") or "").strip()
        if release_mbid:
            con.execute("UPDATE titles SET musicbrainz_id = ? WHERE id = ?",
                        (release_mbid, title_id))
        artists.link(con, title_id, field(form, "artist"),
                     field(form, "artist_mbid"))
        # a brand-new act would otherwise sit there with no id, country or
        # biography until someone pressed a button; a known one costs a read
        linked = con.execute("SELECT artist_id FROM titles WHERE id = ?",
                             (title_id,)).fetchone()
        if linked and linked["artist_id"]:
            try:
                artists.enrich_one(con, linked["artist_id"])
            except Exception as exc:      # a lookup must not lose the title
                print("artists: %s" % exc)

    try:
            owner_id = owner_text
            cursor = con.execute(
                "INSERT INTO items (title_id, owner_id, format, edition,"
                " upc_ean, location, visibility, added_by, price_paid,"
                " acquired_on, notes)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (title_id, int(owner_id), fmt, field(form, "edition") or None,
                 upc, field(form, "location") or None, visibility,
                 viewer["id"], field(form, "price_paid") or None,
                 field(form, "acquired_on") or None,
                 field(form, "notes") or None),
            )
            store = stores.resolve(con, field(form, "store"),
                                   "digital" if fmt == "digital" else "retail")
            shop = stores.by_key(con).get(store) if store else None
            if fmt == "digital" and store:
                con.execute("INSERT INTO digital_sources (item_id, provider)"
                            " VALUES (?, ?)", (cursor.lastrowid, store))
            elif shop:
                # a disc records the shop it was bought from on the copy
                con.execute("UPDATE items SET store_id = ? WHERE id = ?",
                            (shop["id"], cursor.lastrowid))
    except sqlite3.IntegrityError as exc:
        con.rollback()
        if "upc_ean" in str(exc):
            return "That barcode is already in the collection.", None, None
        return "Could not save: %s" % exc, None, None
    con.commit()
    return None, title_id, None


def parse_multipart(body, content_type):
    """Minimal multipart/form-data reader -> {name: [value]}."""
    match = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not match:
        return {}
    delimiter = b"--" + match.group(1).encode()
    fields = {}
    for part in body.split(delimiter):
        if b"\r\n\r\n" not in part:
            continue
        head, data = part.split(b"\r\n\r\n", 1)
        if b"--" in head[:4]:
            continue
        name_match = re.search(rb'name="([^"]*)"', head)
        if not name_match:
            continue
        name = name_match.group(1).decode("utf-8", "replace")
        # a part that names a file is kept as bytes: decoding a picture
        # with "replace" would destroy it, and an upload is the only
        # reason this reader sees one
        payload = data[:-2] if data.endswith(b"\r\n") else data
        if b"filename=" in head:
            fields.setdefault(name, []).append(payload)
        else:
            fields.setdefault(name, []).append(
                payload.decode("utf-8", "replace"))
    return fields


def parse_body(handler):
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length)
    content_type = handler.headers.get("Content-Type") or ""
    if content_type.startswith("multipart/form-data"):
        return parse_multipart(raw, content_type)
    return parse_qs(raw.decode("utf-8"), keep_blank_values=True)


def import_counts(con, import_id):
    counts = {}
    for row in con.execute(
        "SELECT status, COUNT(*) AS n FROM import_rows WHERE import_id = ? GROUP BY status",
        (import_id,),
    ):
        counts[row["status"]] = row["n"]
    return counts


def export_rows(con, viewer_id):
    return con.execute(
        "SELECT t.name AS title, t.release_year, t.kind, t.imdb_id,"
        " i.format, i.edition, i.upc_ean, i.region, i.bluray_com_id, i.condition,"
        " i.location, i.visibility, i.last_watched_on, u.display_name AS owner,"
        " (SELECT group_concat(d.provider, ' ') FROM digital_sources d"
        "   WHERE d.item_id = i.id) AS digital"
        " FROM items i JOIN titles t ON t.id = i.title_id"
        " JOIN users u ON u.id = i.owner_id"
        " WHERE i.visibility = 'shared' OR i.owner_id = ?"
        " ORDER BY t.name COLLATE NATSORT, i.format", (viewer_id,)).fetchall()


EXPORT_COLUMNS = ["Title", "Year", "Kind", "IMDb", "Format", "Edition", "UPC/EAN",
                  "Region", "Blu-ray.com ID", "Condition", "Location", "Owner",
                  "Visibility", "Last watched", "Digital"]


def export_csv(rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_COLUMNS)
    for row in rows:
        writer.writerow([row["title"], row["release_year"] or "", row["kind"] or "",
                         row["imdb_id"] or "", row["format"], row["edition"] or "",
                         row["upc_ean"] or "", row["region"] or "",
                         row["bluray_com_id"] or "", row["condition"] or "",
                         row["location"] or "", row["owner"], row["visibility"],
                         row["last_watched_on"] or "", row["digital"] or ""])
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def export_json(con, viewer_id, viewer_name):
    rows = export_rows(con, viewer_id)
    payload = {
        "exported_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "exported_for": viewer_name,
        "copies": [dict(row) for row in rows],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")


def stats(con):
    return {
        "items": con.execute("SELECT COUNT(*) FROM items").fetchone()[0],
        "titles": con.execute("SELECT COUNT(*) FROM titles").fetchone()[0],
        "digital": con.execute("SELECT COUNT(*) FROM digital_sources").fetchone()[0],
        "owners": con.execute("SELECT COUNT(*) FROM users WHERE is_active = 1").fetchone()[0],
    }



def _with_filed(note, names):
    """Say what a collection refresh filed from the shelf, if anything."""
    if not names:
        return note
    listed = ", ".join(names[:4])
    if len(names) > 4:
        listed += " and %d more" % (len(names) - 4)
    tail = ("Filed %d film%s from the shelf: %s."
            % (len(names), "" if len(names) == 1 else "s", listed))
    return (note + " " + tail) if note else tail


def _back_target(value):
    """Where a title's Back link may point: a ?query, or a page path.

    A plain "?owner=2" comes from the catalogue. "artist/5?view=grid" comes
    from an act's page, which is a different screen rather than a filter, and
    ui.page() builds the link as base + this - so anything that could leave the
    app (a scheme, a host, an absolute path) is refused.
    """
    value = (value or "").strip()
    if not value:
        return None
    if value.startswith("?"):
        return value
    if re.match(r"^[A-Za-z0-9_.~-]+(/[A-Za-z0-9_.~-]+)*"
                r"(\?[^\s\"'<>]*)?$", value):
        return value
    return None


def now_playing_json(con, base, session=""):
    """What the pinned player should show, as JSON for the page's poller.

    `session` is the player the bar was switched to; without one the best
    session is picked (playing before paused, then most recently active), and
    a player that has gone quietly hands the controls back to that one.
    """
    found = jellyfin.active_sessions(con)
    if not found:
        return {"playing": False}
    heard = None
    for item in found:
        if session and item["session"] == session:
            heard = dict(item)
            break
    if heard is None:
        heard = dict(found[0])
    # the bar keys off this: without it every poll looked like silence and the
    # player never appeared at all
    heard["playing"] = True
    # the catalogue usually knows the item, which gives the bar our own cover
    # (a chosen poster, not Jellyfin's) and a link to the title page
    row = con.execute(
        "SELECT t.id, t.name, t.poster_url, t.art_source, ds.external_id"
        "  FROM digital_sources ds"
        "  JOIN items i ON i.id = ds.item_id"
        "  JOIN titles t ON t.id = i.title_id"
        " WHERE ds.provider = 'jellyfin'"
        "   AND ds.external_id IN (?, ?, ?, ?) LIMIT 1",
        (heard["item_id"], heard["album_id"], heard["parent_id"],
         heard.get("series_id") or "")).fetchone()
    if row:
        heard["title_id"] = row["id"]
        heard["title"] = row["name"]
        heard["href"] = base + "title/%d" % row["id"]
        heard["cover"] = ui.pick_cover(base, row["external_id"],
                                       row["poster_url"],
                                       row["art_source"] or "")
    else:
        heard["title"] = heard["name"]
        heard["href"] = ""
        heard["cover"] = base + "art/" + (heard["album_id"] or heard["item_id"])
    # a track is called by its song name, so the act and album are the subtitle;
    # an episode is "Series - S1 E4"; a film only needs the device name
    parts = []
    if heard.get("artist"):
        parts.append(heard["artist"])
    if heard.get("album"):
        parts.append(heard["album"])
    if not parts and heard.get("series"):
        parts.append(heard["series"])
        if heard.get("episode"):
            parts.append("S%s E%s" % (heard.get("season") or 1,
                                      heard["episode"]))
    if not parts:
        parts.append(heard.get("device") or "")
    heard["subtitle"] = " \u00b7 ".join(filter(None, parts))
    # the switcher: every player with something on, the chosen one included,
    # so the bar can show that another player is going and hand control over
    heard["sessions"] = [{"session": item["session"],
                          "device": item["device"],
                          "title": item["name"],
                          "paused": item["paused"]}
                         for item in found[:4]]
    return heard


def _catalogue_back(handler):
    """The catalogue query the viewer last used, as a `?…` back link."""
    for part in (handler.headers.get("Cookie") or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == "mc_back" and value:
            query = unquote(value)
            if query:
                return "?" + query
    return ""


class Handler(BaseHTTPRequestHandler):
    server_version = "MediaCollection/0.1"

    def log_message(self, fmt, *args):
        log("%s - %s" % (self.address_string(), fmt % args))

    def respond(self, code, body, content_type="text/html; charset=utf-8", extra=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        con = connect()
        try:
            viewer = viewer_for(self, con)
            base = base_path(self)
            route = route_path(self)
            match = re.search(r"/art/([A-Za-z0-9]+)$", route)
            if match:
                try:
                    return self.respond(200, jellyfin.image(con, match.group(1)),
                                        "image/jpeg")
                except jellyfin.JellyfinError:
                    return self.respond(404, "", "text/plain; charset=utf-8")
            match = re.search(r"/cover/([A-Za-z0-9._-]+)$", route)
            if match:
                name = match.group(1)
                path = os.path.join(COVERS_DIR, name)
                if not name.startswith(".") and os.path.isfile(path):
                    with open(path, "rb") as handle:
                        blob = handle.read()
                    return self.respond(
                        200, blob,
                        COVER_MIME.get(os.path.splitext(name)[1].lower(),
                                       "image/jpeg"))
                return self.respond(404, "", "text/plain; charset=utf-8")
            if route.rstrip("/").endswith("/nowplaying"):
                wanted = (parse_qs(urlparse(self.path).query).get("session")
                          or [""])[0]
                return self.respond(200,
                                    json.dumps(now_playing_json(con, base,
                                                                wanted)),
                                    "application/json")
            if route.rstrip("/").endswith("/health"):
                return self.respond(200, json.dumps({"status": "ok"}), "application/json")
            if route.rstrip("/").endswith("/barcode"):
                # what a case barcode belongs to, for the add dialog
                asked = (parse_qs(urlparse(self.path).query)
                         .get("upc") or [""])[0]
                code = re.sub(r"\D", "", asked)
                if len(code) not in (12, 13):
                    return self.respond(
                        400, json.dumps(
                            {"error": "A barcode is 12 or 13 digits."}),
                        "application/json")
                here = con.execute(
                    "SELECT t.id, t.name FROM items i"
                    " JOIN titles t ON t.id = i.title_id"
                    " WHERE i.upc_ean = ?"
                    " AND (i.visibility = 'shared' OR i.owner_id = ?)"
                    " LIMIT 1", (code, viewer["id"])).fetchone()
                match, note = metadata.upc_lookup(code)
                return self.respond(200, json.dumps({
                    "upc": code,
                    "found": match,
                    "note": note,
                    "existing": dict(here) if here else None,
                }), "application/json")

            if route.rstrip("/").endswith("/api/state"):
                payload = {
                    "viewer": viewer["display_name"],
                    "count": len(library_rows(con, viewer["id"])),
                    "owners": [dict(o) for o in owner_totals(con)],
                }
                return self.respond(200, json.dumps(payload), "application/json")

            if route.rstrip("/").endswith("/admin/status.json"):
                return self.respond(200, json.dumps(refresh_job.status()),
                                    "application/json")

            if route.rstrip("/").endswith("/admin"):
                params = parse_qs(urlparse(self.path).query)
                deduped = (params.get("deduped") or ["0"])[0]
                message = None
                if deduped.isdigit() and int(deduped):
                    message = "Folded %d duplicate title(s)." % int(deduped)
                elif params.get("jellyfin"):
                    message = params["jellyfin"][0]
                elif params.get("players"):
                    message = params["players"][0]
                elif params.get("artists"):
                    message = params["artists"][0]
                elif params.get("scan") and scan.summary():
                    message = scan.summary()
                confirm = ""
                asked = (params.get("confirm_root") or [""])[0]
                if asked.isdigit():
                    root = con.execute("SELECT id, label FROM media_roots"
                                       " WHERE id = ?",
                                       (int(asked),)).fetchone()
                    if root:
                        files = con.execute(
                            "SELECT COUNT(*) FROM digital_sources"
                            " WHERE root_id = ? AND provider = 'rip'",
                            (root["id"],)).fetchone()[0]
                        copies = con.execute(
                            "SELECT COUNT(DISTINCT item_id)"
                            " FROM digital_sources"
                            " WHERE root_id = ? AND provider = 'rip'",
                            (root["id"],)).fetchone()[0]
                        confirm = ui_admin.confirm_root(
                            base, root, files, copies)
                page = ui_admin.render(
                    base, viewer, stats(con), message,
                    refresh_job.status(), len(dedupe.groups(con)),
                    con.execute("SELECT * FROM media_roots"
                                " ORDER BY medium, label").fetchall(),
                    jellyfin.status(con), scan.summary(),
                    players=playback.players(),
                    entities=playback.entities(),
                    chosen=playback.chosen(con),
                    jobs={"scan": scan.status(),
                          "artists": artists.status(),
                          "jellyfin": jellyfin.progress(),
                          "clean": clean_job.status()},
                    store_rows=with_players(con),
                    targets=stores.targets(con),
                    confirm=confirm,
                    # a saved player list keeps the panel open (the redirect
                    # carries `?players=…`, empty when everything was unticked)
                    open_players="players=" in (urlparse(self.path).query
                                                or ""))
                return self.respond(200, page)

            if route.rstrip("/").endswith("/export.csv"):
                return self.respond(
                    200, export_csv(export_rows(con, viewer["id"])),
                    "text/csv; charset=utf-8",
                    {"Content-Disposition": 'attachment; filename="media-collection-%s.csv"'
                     % datetime.now().strftime("%Y%m%d")})

            if route.rstrip("/").endswith("/export.json"):
                return self.respond(
                    200, export_json(con, viewer["id"], viewer["display_name"]),
                    "application/json",
                    {"Content-Disposition": 'attachment; filename="media-collection-%s.json"'
                     % datetime.now().strftime("%Y%m%d")})
            query = (parse_qs(urlparse(self.path).query).get("q") or [""])[0].strip()
            view = (parse_qs(urlparse(self.path).query).get("view") or ["grid"])[0]
            kind = (parse_qs(urlparse(self.path).query).get("kind") or [""])[0]
            owner_raw = (parse_qs(urlparse(self.path).query).get("owner") or [""])[0]
            owner = int(owner_raw) if owner_raw.isdigit() else None
            fmt = ",".join(f for f in parse_qs(urlparse(self.path).query)
                           .get("format", []) if f)
            only = bool(parse_qs(urlparse(self.path).query).get("only"))
            if route.rstrip("/").endswith("/artists"):
                asked = parse_qs(urlparse(self.path).query)
                want = (asked.get("view") or ["grid"])[0]
                search = (asked.get("q") or [""])[0].strip()
                rows = artists.all_artists(con, viewer["id"])
                held = rows        # every act, so every owner keeps a chip
                # The search narrows the acts before anything is counted, or
                # the owner chips on this page keep the whole-shelf numbers
                # under a list of three acts (the catalogue's three rows of
                # pills were made to agree with the search in 0.23.154).
                if search:
                    needle = search.lower()
                    rows = [row for row in rows
                            if needle in (row["name"] or "").lower()]
                shown = {row["id"] for row in rows}
                total = len(rows)
                owner_rows, owner_id = [], None
                if asked.get("owner"):
                    try:
                        owner_id = int(asked["owner"][0])
                    except ValueError:
                        owner_id = None
                # "Ripped files" is the account the Jellyfin and file copies
                # belong to, not a person, so it gets no chip of its own - the
                # catalogue's owner row leaves it out for the same reason
                people = {row["id"]: row for row in con.execute(
                    "SELECT id, display_name, username FROM users")}
                tally = {}
                for row in held:
                    for oid in set((row["owner_ids"] or "").split(",")):
                        person = (people.get(int(oid))
                                  if oid.isdigit() else None)
                        if person is None or person["username"] == "rips":
                            continue
                        tally.setdefault(oid, 0)
                        if row["id"] in shown:
                            tally[oid] += 1
                if tally:
                    owner_rows = [
                        {"id": int(oid),
                         "name": people[int(oid)]["display_name"],
                         "count": n}
                        for oid, n in sorted(tally.items(), key=lambda kv: -kv[1])]
                if owner_id is not None:
                    rows = [row for row in rows
                            if str(owner_id) in (row["owner_ids"] or "").split(",")]
                return self.respond(200, ui_artists.render_list(
                    base, rows,
                    "grid" if want == "grid" else "list", search,
                    owner_rows, owner_id, total))

            match = re.search(r"/artist/(\d+)/?$", route)
            if match:
                artist = artists.get(con, int(match.group(1)))
                if artist is None:
                    return self.respond(404, "No such artist",
                                        "text/plain; charset=utf-8")
                want = (parse_qs(urlparse(self.path).query).get("view")
                        or ["grid"])[0]
                asked = parse_qs(urlparse(self.path).query)
                notice = (asked.get("mbid")
                          or asked.get("mbid_error") or [""])[0]
                return self.respond(200, ui_artists.render_artist(
                    base, artist,
                    artists.titles_for(con, artist["id"], viewer["id"]),
                    FORMAT_LABELS, "grid" if want == "grid" else "list",
                    notice, bool(asked.get("mbid_error"))))

            # Film collections. A collection is a series (Fast & Furious,
            # Alien) and holds the titles that point at it; the page lists
            # them in watching order, which a name search cannot do.
            series = re.search(r"/collections/?$", route)
            if series:
                term = (parse_qs(urlparse(self.path).query).get("q")
                        or [""])[0].strip()
                rows = boxsets.all_collections(con, viewer["id"], term)
                return self.respond(200, ui_boxsets.render_list(base, rows, term))

            one = re.search(r"/collection/(\d+)/?$", route)
            if one:
                collection_id = int(one.group(1))
                row = boxsets.get(con, collection_id)
                if row is None:
                    return self.respond(404, "No such collection",
                                        "text/plain; charset=utf-8")
                asked = parse_qs(urlparse(self.path).query)
                query = (asked.get("q") or [""])[0].strip()
                find = (asked.get("find") or [""])[0].strip()
                # a lookup that failed says so in red, not as good news
                error = (asked.get("error") or [""])[0]
                found = (boxsets.candidates(con, find, viewer["id"],
                                            collection_id) if find else [])
                return self.respond(200, ui_boxsets.render_collection(
                    base, row,
                    boxsets.titles_for(con, collection_id, viewer["id"]),
                    FORMAT_LABELS,
                    notice=error or (asked.get("notice") or [""])[0],
                    bad=bool(error),
                    found=found, find=find, query=query,
                    series=boxsets.series_summary(con, collection_id,
                                                  viewer["id"])))

            covers = re.search(r"/title/(\d+)/covers/?$", route)
            if covers:
                asked = (parse_qs(urlparse(self.path).query).get("q")
                         or [""])[0].strip()
                return self.respond(200, self.cover_page(
                    con, viewer, base, int(covers.group(1)), asked))
            # detail page, always last so /title/<id>/metadata is handled in POST
            linking = re.search(r"/title/(\d+)/link/?$", route)
            if linking:
                term = (parse_qs(urlparse(self.path).query)
                        .get("jfq") or [""])[0].strip()
                notice = (parse_qs(urlparse(self.path).query)
                          .get("error") or [""])[0].strip()
                return self.respond(200, self.link_page(
                    con, viewer, base, int(linking.group(1)), term,
                    notice))
            detail = re.search(r"/title/(\d+)/?$", route)
            if detail:
                return self.title_page(con, viewer, base, int(detail.group(1)),
                                       owner=owner)
            return self.respond(200, render_index(
                con, viewer, base, query=query,
                view="grid" if view == "grid" else "list",
                owner=owner,
                kind=kind if kind in ("movie", "tv", "music", "other") else "movie",
                format=fmt, only=only),
                extra={"Set-Cookie": "mc_back=%s; Path=%s; SameSite=Lax"
                       % (quote(urlparse(self.path).query, safe=""),
                          base or "/")})
        finally:
            con.close()

    def link_page(self, con, viewer, base, title_id, term="", notice=""):
        """Pick a Jellyfin item by hand when the automatic match missed."""
        row = con.execute("SELECT name FROM titles WHERE id = ?",
                          (title_id,)).fetchone()
        copies = con.execute(
            "SELECT i.id, i.format, u.display_name AS owner"
            "  FROM items i JOIN users u ON u.id = i.owner_id"
            " WHERE i.title_id = ? ORDER BY i.id", (title_id,)).fetchall()
        results, note = [], ""
        if term:
            try:
                results = jellyfin.search_items(con, term)
                note = "%d match%s" % (len(results),
                                       "" if len(results) == 1 else "es")
            except Exception as exc:
                note = "Jellyfin said: %s" % exc
        if notice:
            note = "%s %s" % (notice, note) if note else notice
        rows = "".join(
            '<li><strong>%s</strong> <span class="muted">%s%s &#183; %s'
            '</span> <form method="post" action="%stitle/%d/link"'
            ' style="display:inline"><input type="hidden" name="jf_id"'
            ' value="%s">'
            '<button type="submit">Link</button></form></li>'
            % (html.escape(item["name"]), html.escape(item["kind"]),
               " (%s)" % item["year"] if item["year"] else "",
               html.escape((item["path"] or "")[-70:]), html.escape(base),
               title_id, item["id"])
            for item in results)
        body = ('<h2>Link %s to Jellyfin</h2>'
                '<form method="get" action="%stitle/%d/link">'
                '<label>Search Jellyfin<input name="jfq" value="%s"></label> '
                '<button type="submit">Search</button></form>'
                '<p class="sub">%s</p><ul class="routes">%s</ul>'
                % (html.escape(row["name"] if row else ""), html.escape(base),
                   title_id, html.escape(term), html.escape(note), rows))
        return ui.page(base, "Link to Jellyfin", body,
                       back="title/%d" % title_id)

    def cover_page(self, con, viewer, base, title_id, term=""):
        """Every cover the keyless sources hold for one title, to pick from.

        A title can carry one picture, and the automatic lookup only ever finds
        the standard edition's - so this page lists every edition the two free
        sources know, and any of them can be chosen with one click.
        """
        row = con.execute(
            "SELECT t.*, a.name AS artist_name FROM titles t"
            "  LEFT JOIN artists a ON a.id = t.artist_id"
            " WHERE t.id = ?", (title_id,)).fetchone()
        if row is None:
            return self.respond(404, "No such title",
                                "text/plain; charset=utf-8")
        artist_mbid = ""
        if row["artist_id"]:
            looked = con.execute("SELECT musicbrainz_id FROM artists"
                                 " WHERE id = ?",
                                 (row["artist_id"],)).fetchone()
            if looked:
                artist_mbid = (looked["musicbrainz_id"] or "").strip()
        found, note = metadata.cover_search(row, term, artist_mbid or None)
        tiles = "".join(
            '<form method="post" action="%stitle/%d/art">'
            '<input type="hidden" name="source" value="poster">'
            '<input type="hidden" name="url" value="%s">'
            '<button type="submit" title="Use this one">'
            '<img src="%s" alt="" loading="lazy">'
            '<span>%s</span></button></form>'
            % (html.escape(base), title_id, html.escape(item["url"]),
               html.escape(item["url"]), html.escape(item["label"]))
            for item in found)
        words = (term or "").strip() or " ".join(
            part for part in ((row["artist_name"] or "").strip(),
                              (row["name"] or "").strip()) if part)
        body = ('<h2>Covers for %s</h2>'
                '<form method="get" action="%stitle/%d/covers">'
                '<label>Search<input name="q" value="%s"></label> '
                '<button type="submit">Search</button></form>'
                '<p class="sub">%s</p>'
                '<div class="covers">%s</div>'
                % (html.escape(row["name"] or ""), html.escape(base), title_id,
                   html.escape(words), html.escape(note or ""), tiles))
        if not found:
            body += ('<p class="sub">Try the search box: a shop files its cover '
                     'under the name it sells it by, which is not always the '
                     'name on the shelf.</p>')
        body += ('<section class="panel"><h2>Upload a picture</h2>'
                 '<form class="upload" method="post"'
                 ' enctype="multipart/form-data" action="%stitle/%d/cover">'
                 '<label>Image file<input type="file" name="image" required'
                 ' accept="image/png,image/jpeg,image/webp,image/gif"></label>'
                 '<button type="submit">Upload</button></form>'
                 '<p class="sub">A JPEG, PNG, WebP or GIF up to 6 MB. It is kept '
                 'with the catalogue, so it does not depend on the page it came '
                 'from.</p></section>'
                 '<section class="panel"><h2>Use an image from a link</h2>'
                 '<form method="post" action="%stitle/%d/art">'
                 '<input type="hidden" name="source" value="poster">'
                 '<label>Image address<input type="url" name="url"'
                 ' placeholder="https://example.com/cover.jpg"></label> '
                 '<button type="submit">Use this image</button></form>'
                 '<p class="sub">Any address a browser can reach - a Wikipedia '
                 'file, a record shop, a scan you put online somewhere.</p>'
                 '</section>'
                 % (html.escape(base), title_id, html.escape(base), title_id))
        return ui.page(base, "Cover art", body, back="title/%d" % title_id,
                       back_label="\u2190 Back to the title")

    def title_page(self, con, viewer, base, title_id, summary=None, error=None,
                   owner=None):
        row = con.execute(
            "SELECT t.*, a.name AS artist_name, c.name AS collection_name"
            " FROM titles t"
            "  LEFT JOIN artists a ON a.id = t.artist_id"
            "  LEFT JOIN collections c ON c.id = t.collection_id"
            " WHERE t.id = ?", (title_id,)).fetchone()
        if row is None:
            return self.respond(404, "No such title", "text/plain; charset=utf-8")
        items = con.execute(
            "SELECT i.*, u.display_name AS owner_name FROM items i"
            " JOIN users u ON u.id = i.owner_id WHERE i.title_id = ?"
            " AND (i.visibility = 'shared' OR i.owner_id = ?)"
            " ORDER BY i.format, i.id",
            (title_id, viewer["id"])).fetchall()
        digital = con.execute(
            "SELECT d.*, i.format AS copy_format,"
            " u.display_name AS owner_name,"
            " r.label AS root_label, r.path AS root_path,"
            " (SELECT l.jf_name FROM jellyfin_links l"
            "   WHERE l.jf_id = d.external_id LIMIT 1) AS jf_name,"
            " (SELECT l.jf_path FROM jellyfin_links l"
            "   WHERE l.jf_id = d.external_id LIMIT 1) AS jf_path,"
            " (SELECT l.jf_kind FROM jellyfin_links l"
            "   WHERE l.jf_id = d.external_id LIMIT 1) AS jf_kind"
            " FROM digital_sources d JOIN items i ON i.id = d.item_id"
            " JOIN users u ON u.id = i.owner_id"
            " LEFT JOIN media_roots r ON r.id = d.root_id"
            " WHERE i.title_id = ? ORDER BY d.provider", (title_id,)).fetchall()
        roots = con.execute("SELECT id, label, path, medium FROM media_roots"
                            " ORDER BY medium, label").fetchall()
        try:
            jellyfin_link = jellyfin.link_for_title(con, title_id)
            jellyfin_sessions = jellyfin.sessions(con) if jellyfin_link else []
        except Exception as exc:                     # never break the page for this
            print("jellyfin: %s" % exc)
            jellyfin_link, jellyfin_sessions = None, []
        tab = (parse_qs(urlparse(self.path).query).get("tab")
               or ["about"])[0]
        source = (parse_qs(urlparse(self.path).query).get("from")
                  or [""])[0]
        back = _back_target(source)
        if back is None:  # a plain card link carries no filter, so ask the browser
            seen = urlparse(self.headers.get("Referer") or "")
            if seen.query and not seen.path.startswith("/title"):
                back = "?" + seen.query
        if back is None:    # no referer (a fresh tab, a bookmark): use the cookie
            back = _catalogue_back(self) or None
        return self.respond(200, ui_title.render(
            base, row, items, digital, FORMATS, FORMAT_LABELS, summary=summary,
            error=error, ripped_root=RIPPED_ROOT, roots=roots, owner=owner,
            players=playback.players_for(con), last_player=playback.last_player(con),
            jellyfin_link=jellyfin_link, jellyfin_sessions=jellyfin_sessions,
            jellyfin_configured=bool(jellyfin.config(con)[1]),
            users=con.execute("SELECT id, display_name FROM users"
                              " ORDER BY display_name").fetchall(),
            viewer_id=viewer["id"], tab=tab, back=back,
            stores=stores.load(con),
            shops=stores.playable_stores(con, store_keys(con, digital, items)),
            collections=boxsets.choices(con, row["kind"]),
            back_label=("\u2190 Back to the artist"
                        if (back or "").startswith("artist/")
                        else ("\u2190 Back to the collection"
                              if (back or "").startswith("collection/")
                              else "\u2190 Back to the catalogue"))))

    def do_POST(self):
        form = parse_body(self)
        con = connect()
        try:
            viewer = viewer_for(self, con)
            # Film collections. A collection is a series (Fast & Furious,
            # Alien); it is made on the collections page, renamed and deleted
            # from its own page, and picked on a film's page.
            made = re.search(r"/collections/?$", route_path(self))
            if made:
                name = field(form, "name")
                where = base_path(self) + "collections"
                if name:
                    where = (base_path(self) + "collection/%d"
                             % boxsets.resolve(con, name,
                                               field(form, "kind") or "movie"))
                return self.respond(303, "", extra={"Location": where})

            added = re.search(r"/collection/(\d+)/add$", route_path(self))
            if added:
                collection_id = int(added.group(1))
                title_id = field(form, "title_id")
                if title_id.isdigit():
                    boxsets.link_title(con, int(title_id), collection_id,
                                       field(form, "collection_order"))
                return self.respond(303, "", extra={
                    "Location": base_path(self) + "collection/%d" % collection_id})

            enriched = re.search(r"/collection/(\d+)/metadata$",
                                 route_path(self))
            if enriched:
                collection_id = int(enriched.group(1))
                where = base_path(self) + "collection/%d" % collection_id
                row = boxsets.get(con, collection_id)
                note = ""
                bad = ""
                try:
                    summary = metadata.refresh_collection(
                        con, collection_id, row)
                except metadata.Throttle:
                    bad = ("Wikimedia is rate-limiting the lookups - try "
                           "again in a few minutes.")
                    summary = None
                if summary is not None:
                    if not summary["page"]:
                        bad = ("No Wikipedia article for this series - type "
                               "the paragraph in *Edit this collection* "
                               "instead.")
                    elif summary["filled"]:
                        note = ("Filled the %s from Wikipedia's \"%s\" "
                                "article." % (" and the ".join(
                                    summary["filled"]),
                                    summary["article"]))
                    else:
                        have = [label for label, value in
                                (("paragraph", row["overview"]),
                                 ("cover", row["poster_url"]))
                                if (value or "").strip()]
                        if have:
                            note = ("Nothing to fill - the collection "
                                    "already has its %s, and the article "
                                    "offers no more. Type it under *Edit this "
                                    "collection*." % " and its ".join(have))
                        else:
                            note = ("The article had nothing to add - type "
                                    "the paragraph in *Edit this "
                                    "collection*.")
                    note = _with_filed(note, summary["filed"])
                if bad:
                    where += "?error=" + quote(bad)
                elif note:
                    where += "?notice=" + quote(note)
                return self.respond(303, "", extra={"Location": where})

            looked = re.search(r"/collection/(\d+)/members$",
                               route_path(self))
            if looked:
                # What is in this series that the shelf has not got, plus the
                # paragraph and the films this pass could file. One call, so
                # the two buttons and the nightly pass cannot drift apart.
                collection_id = int(looked.group(1))
                where = base_path(self) + "collection/%d" % collection_id
                row = boxsets.get(con, collection_id)
                note = ""
                bad = ""
                try:
                    summary = metadata.refresh_collection(
                        con, collection_id, row)
                except metadata.Throttle:
                    bad = ("Wikimedia is rate-limiting the lookups - try "
                           "again in a few minutes.")
                    summary = None
                if summary is not None:
                    if not summary["qid"]:
                        bad = ("Wikidata has no film series called \"%s\" - "
                               "the collection's own name may be the place "
                               "to look." % (row["name"] if row else ""))
                    else:
                        count = summary["members"]
                        note = ("Wikidata files %d film%s under this series."
                                % (count, "" if count == 1 else "s"))
                        note = _with_filed(note, summary["filed"])
                if bad:
                    where += "?error=" + quote(bad)
                elif note:
                    where += "?notice=" + quote(note)
                return self.respond(303, "", extra={"Location": where})

            series = re.search(r"/collection/(\d+)/?$", route_path(self))
            if series:
                collection_id = int(series.group(1))
                if field(form, "delete"):
                    boxsets.remove(con, collection_id)
                    return self.respond(303, "", extra={
                        "Location": base_path(self) + "collections"})
                if field(form, "name"):
                    boxsets.rename(con, collection_id, field(form, "name"))
                boxsets.set_notes(con, collection_id, field(form, "notes"))
                return self.respond(303, "", extra={
                    "Location": base_path(self) + "collection/%d" % collection_id})

            lookup = re.search(r"/title/(\d+)/collection/lookup$",
                               route_path(self))
            if lookup:
                title_id = int(lookup.group(1))
                base = base_path(self)
                held = con.execute(
                    "SELECT c.name AS name FROM titles t"
                    "  JOIN collections c ON c.id = t.collection_id"
                    " WHERE t.id = ?", (title_id,)).fetchone()
                if held:
                    return self.title_page(
                        con, viewer, base, title_id,
                        summary=("Already in %s - pick \"-\" above to take it "
                                 "out before Wikidata is asked." % held["name"]))
                try:
                    made = metadata.autofill_series(con, title_id, force=True)
                    note = ("Filed under %s." % made if made else
                            "Wikidata files this film under no series.")
                except metadata.Throttle:
                    note = ("Wikidata asked us to slow down - try again in a "
                            "minute.")
                except Exception as exc:
                    note = "Could not ask Wikidata: %s" % exc
                return self.title_page(con, viewer, base, title_id,
                                       summary=note)

            picked = re.search(r"/title/(\d+)/collection$", route_path(self))
            if picked:
                title_id = int(picked.group(1))
                name = (field(form, "new_collection")
                        or field(form, "collection"))
                if name:
                    collection_id = boxsets.resolve(con, name,
                                                    field(form, "kind") or "movie")
                    boxsets.link_title(con, title_id, collection_id,
                                       field(form, "collection_order"))
                else:
                    boxsets.unlink_title(con, title_id)
                return self.respond(303, "", extra={
                    "Location": base_path(self) + "title/%d" % title_id})

            pressed = re.search(r"/nowplaying/?$", route_path(self))
            if pressed:
                try:
                    jellyfin.control(con, field(form, "session_id"),
                                     field(form, "action"))
                except jellyfin.JellyfinError as exc:
                    return self.respond(200, json.dumps(
                        {"ok": False, "note": str(exc)}), "application/json")
                return self.respond(200, json.dumps({"ok": True}),
                                    "application/json")
            asked = re.search(r"/artist/(\d+)/bio/?$", route_path(self))
            if asked:
                artist_id = int(asked.group(1))
                try:
                    artists.fill_bio(con, artist_id=artist_id)
                except Exception as exc:       # a lookup must not kill the page
                    print("artists: %s" % exc)
                return self.respond(303, "", extra={
                    "Location": base_path(self) + "artist/%d" % artist_id})
            renamed = re.search(r"/artist/(\d+)/?$", route_path(self))
            if renamed:
                artist_id = int(renamed.group(1))
                where = base_path(self) + "artist/%d" % artist_id
                wanted = (field(form, "artist_name") or "").strip()
                if wanted:
                    artists.rename(con, artist_id, wanted)
                elif field(form, "action") == "mbid":
                    # the id form; a blank box clears it on purpose
                    ok, message = artists.set_mbid(con, artist_id,
                                                   field(form, "musicbrainz_id"))
                    where += "?%s=%s" % ("mbid" if ok else "mbid_error",
                                        quote(message))
                return self.respond(303, "", extra={"Location": where})
            gone = re.search(r"/title/(\d+)/unlink/?$", route_path(self))
            if gone:
                source = (field(form, "source_id") or "").strip()
                if source.isdigit():
                    found = con.execute(
                        "SELECT item_id FROM digital_sources WHERE id = ?",
                        (int(source),)).fetchone()
                    if found:
                        con.execute("DELETE FROM digital_sources"
                                    " WHERE id = ?", (int(source),))
                        con.execute("DELETE FROM jellyfin_links"
                                    " WHERE item_id = ?",
                                    (found["item_id"],))
                        left = con.execute(
                            "SELECT count(*) FROM digital_sources"
                            " WHERE item_id = ?",
                            (found["item_id"],)).fetchone()[0]
                        item = con.execute(
                            "SELECT format, rip_path FROM items"
                            " WHERE id = ?",
                            (found["item_id"],)).fetchone()
                        if (not left and item and item["format"] == "ripped"
                                and not item["rip_path"]):
                            con.execute("DELETE FROM items WHERE id = ?",
                                        (found["item_id"],))
                        con.commit()
                return self.respond(303, "", extra={
                    "Location": base_path(self) + "title/%d"
                    % int(gone.group(1))})
            linked = re.search(r"/title/(\d+)/link/?$", route_path(self))
            if linked:
                wanted = (field(form, "jf_id") or "").strip()
                if wanted:
                    # a Jellyfin copy is a copy of its own: add one
                    # rather than bolting the route onto a disc copy
                    made, problem = None, ""
                    try:
                        made = jellyfin.link_item(con, int(linked.group(1)),
                                                  wanted)
                    except Exception as exc:
                        print("jellyfin link failed: %s" % exc)
                        problem = str(exc)
                    if made is None and not problem:
                        held = con.execute(
                            "SELECT t.id, t.name FROM digital_sources d"
                            " JOIN items i ON i.id = d.item_id"
                            " JOIN titles t ON t.id = i.title_id"
                            " WHERE d.provider = 'jellyfin'"
                            "   AND d.external_id = ? LIMIT 1",
                            (wanted,)).fetchone()
                        problem = ("Not linked: %s is already linked to \"%s\""
                                   " (#%s). Unlink it there first."
                                   % (wanted, held["name"], held["id"])
                                   if held else
                                   "Not linked: %s is already linked somewhere."
                                   % wanted)
                    if problem:
                        from urllib.parse import quote as _q
                        return self.respond(303, "", extra={"Location":
                            base_path(self) + "title/%s/link?error=%s"
                            % (linked.group(1), _q(problem))})
                return self.respond(303, "", extra={
                    "Location": base_path(self) + "title/%d"
                    % int(linked.group(1))})
            base = base_path(self)
            route = route_path(self)

            if route.rstrip("/").endswith("/admin/roots"):
                label = field(form, "label")
                path = field(form, "path")
                if not (label and path):
                    return self.respond(400, "Label and path are required.",
                                        "text/plain; charset=utf-8")
                try:
                    con.execute("INSERT INTO media_roots (label, path)"
                                " VALUES (?, ?)", (label, path))
                    con.commit()
                except sqlite3.IntegrityError:
                    con.rollback()
                return self.respond(303, "", extra={"Location": base + "admin#roots"})

            match = re.search(r"/admin/roots/(\d+)/?$", route)
            if match:
                root_id = int(match.group(1))
                label = field(form, "label")
                path = field(form, "path")
                if not (label and path):
                    return self.respond(400, "Label and path are required.",
                                        "text/plain; charset=utf-8")
                try:
                    con.execute("UPDATE media_roots SET label = ?, path = ?"
                                " WHERE id = ?", (label, path, root_id))
                    con.commit()
                except sqlite3.IntegrityError:
                    con.rollback()
                    return self.respond(
                        400, "Another root already uses that label or path.",
                        "text/plain; charset=utf-8")
                return self.respond(303, "", extra={"Location": base + "admin#roots"})

            if route.rstrip("/").endswith("/admin/clean"):
                # a long check belongs in the background so the page can
                # show how far it has got
                clean_job.start(DB_PATH)
                return self.respond(303, "", extra={
                    "Location": (base or "/") + "admin?clean=1#maintenance"})

            match = re.search(r"/admin/roots/(\d+)/delete/?$", route)
            if match:
                root_id = int(match.group(1))
                choice = field(form, "choice")
                if choice not in ("files", "keep"):
                    # ask first, the way a Kodi source removal does
                    return self.respond(303, "", extra={
                        "Location": (base or "/")
                        + "admin?confirm_root=%d#roots" % root_id})
                if choice == "keep":
                    con.execute("UPDATE digital_sources SET root_id = NULL"
                                " WHERE root_id = ?", (root_id,))
                else:
                    con.execute("DELETE FROM digital_sources"
                                " WHERE root_id = ? AND provider = 'rip'", (root_id,))
                    con.execute("UPDATE items SET rip_path = NULL,"
                                " rip_root_id = NULL WHERE rip_root_id = ?",
                                (root_id,))
                con.execute("DELETE FROM media_roots WHERE id = ?", (root_id,))
                con.commit()
                pruned = dedupe.orphans(con)
                return self.respond(303, "", extra={
                    "Location": (base or "/") + "admin?pruned=%d#roots" % pruned})
            if route.rstrip("/").endswith("/admin/dedupe"):
                folded = dedupe.merge(con)
                return self.respond(303, "", extra={
                    "Location": base + "admin?deduped=%d#maintenance" % folded})

            if route.rstrip("/").endswith("/admin/refresh"):
                # one button, three lookups: the titles (Wikipedia/Wikidata for
                # films and TV, MusicBrainz for albums), the acts themselves
                # and every collection's films, paragraph and cover
                refresh_job.start(DB_PATH, force=bool(field(form, "force")),
                                  collections=True)
                artists.start(DB_PATH)
                return self.respond(303, "", extra={"Location": base + "admin#metadata"})

            if route.rstrip("/").endswith("/admin/players"):
                # the admin page posts the ticked players; the route has to
                # exist or the form lands on "Not found"
                if "clear" in form:
                    picked = []
                else:
                    picked = [v.strip() for v in form.get("player", [])
                              if v and v.strip()]
                playback.choose(con, picked)
                # a relative Location resolves against /admin/players and
                # lands on /admin/admin: go absolute
                where = (base or "/") + "admin?players=%d#players" % len(picked)
                return self.respond(303, "", extra={"Location": where})

            if route.rstrip("/").endswith("/admin/scan"):
                has_roots = con.execute(
                    "SELECT 1 FROM media_roots LIMIT 1").fetchone()
                if has_roots is None:
                    # nothing to walk: bounce back rather than start a
                    # job that can only fail
                    return self.respond(303, "", extra={
                        "Location": base + "admin#roots"})
                scan.start(DB_PATH, repair=True)
                return self.respond(303, "", extra={"Location": base + "admin?scan=1#roots"})

            if route.rstrip("/").endswith("/jellyfin/save"):
                url = field(form, "jellyfin_url").strip()
                key = field(form, "jellyfin_api_key").strip()
                for name, value in (("jellyfin_url", url), ("jellyfin_api_key", key)):
                    if value:
                        con.execute("INSERT OR REPLACE INTO settings (key, value)"
                                    " VALUES (?, ?)", (name, value))
                con.commit()
                return self.respond(303, "", extra={
                    "Location": base + "admin?jellyfin=#jellyfin"
                    + quote("Jellyfin settings saved - press Sync to match the copies.")})

            if route.rstrip("/").endswith("/admin/artists"):
                artists.start(DB_PATH)
                # no banner, and no jump to the top: land on the block
                return self.respond(303, "", extra={
                    "Location": base + "admin#artists"})

                if field(form, "clear"):
                    playback.choose(con, [])
                else:
                    picked = form.get("player") or []
                    saved = playback.chosen(con) or []
                    listed = {player["entity_id"]
                              for player in playback.players()}
                    playback.choose(con, picked + [name for name in saved
                                                   if name not in listed])
                return self.respond(303, "", extra={
                    "Location": base + "admin?players=#players"
                    + quote("Player list saved.")})

            match = re.search(r"/stores/device/(\d+)/?$", route.rstrip("/"))
            if match:
                entity = field(form, "ha_entity_id")
                domain = entity.split(".", 1)[0] if "." in entity else ""
                stores.update_target(
                    con, int(match.group(1)),
                    name=field(form, "name") or None,
                    kind=domain or field(form, "kind") or None,
                    ha_entity_id=entity or None,
                    input_source=field(form, "input_source"),
                    commit=True)
                return self.respond(303, "", extra={
                    "Location": (base or "/") + "admin#stores"})

            match = re.search(r"/stores/device/(\d+)/delete/?$", route)
            if match:
                con.execute("DELETE FROM playback_targets WHERE id = ?",
                            (int(match.group(1)),))
                con.commit()
                return self.respond(303, "", extra={
                    "Location": (base or "/") + "admin#stores"})

            if route.rstrip("/").endswith("/stores/device"):
                entity = field(form, "ha_entity_id")
                domain = entity.split(".", 1)[0] if "." in entity else ""
                name = field(form, "name") or (
                    playback.entity_name(entity) if entity else "")
                if name:
                    stores.add_target(con, name,
                                      domain or field(form, "kind")
                                      or "media_player",
                                      entity, field(form, "app_name"),
                                      field(form, "input_source"), commit=True)
                return self.respond(303, "", extra={
                    "Location": (base or "/") + "admin#stores"})

            match = re.search(r"/stores/(\d+)/link/(\d+)$", route.rstrip("/"))
            if match:
                stores.set_link_app(con, int(match.group(1)),
                                    int(match.group(2)),
                                    field(form, "app"), commit=True)
                return self.respond(303, "", extra={
                    "Location": (base or "/") + "admin#stores"})

            match = re.search(r"/stores/(\d+)/attach/?$", route)
            if match:
                picked = field(form, "target_id")
                if picked.isdigit():
                    stores.attach(con, int(match.group(1)), int(picked),
                                  field(form, "app"), commit=True)
                return self.respond(303, "", extra={
                    "Location": (base or "/") + "admin#stores"})

            match = re.search(r"/stores/(\d+)/detach/(\d+)/?$", route)
            if match:
                stores.detach(con, int(match.group(1)), int(match.group(2)),
                              commit=True)
                return self.respond(303, "", extra={
                    "Location": (base or "/") + "admin#stores"})

            match = re.search(r"/stores/(\d+)/delete/?$", route)
            if match:
                stores.remove(con, int(match.group(1)), commit=True)
                return self.respond(303, "", extra={
                    "Location": (base or "/") + "admin#stores"})

            match = re.search(r"/stores/(\d+)/?$", route)
            if match:
                stores.update(con, int(match.group(1)),
                              label_text=field(form, "label"),
                              kind=field(form, "kind") or None,
                              app_name=field(form, "app_name"), commit=True)
                return self.respond(303, "", extra={
                    "Location": (base or "/") + "admin#stores"})

            if route.rstrip("/").endswith("/stores"):
                stores.add(con, field(form, "label"),
                           kind=field(form, "kind") or "digital",
                           app_name=field(form, "app_name"), commit=True)
                return self.respond(303, "", extra={
                    "Location": (base or "/") + "admin#stores"})


            if route.rstrip("/").endswith("/jellyfin/server"):
                label = field(form, "label") or "Jellyfin"
                url = field(form, "url").rstrip("/")
                key = field(form, "api_key")
                if url:
                    con.execute("INSERT INTO jellyfin_servers (label, url, api_key)"
                                " VALUES (?, ?, ?)", (label, url, key))
                    con.commit()
                return self.respond(303, "", extra={
                    "Location": (base or "/") + "admin#jellyfin"})

            match = re.search(r"/jellyfin/server/(\d+)/delete/?$", route)
            if match:
                # a server goes, and the copies only it backed go with it
                jellyfin.forget_server(con, int(match.group(1)))
                return self.respond(303, "", extra={
                    "Location": (base or "/") + "admin#jellyfin"})

            match = re.search(r"/jellyfin/server/(\d+)$", route.rstrip("/"))
            if match:
                # a rotated key or a moved address, changed in place
                row = con.execute(
                    "SELECT id, label, url, api_key FROM jellyfin_servers"
                    " WHERE id = ?", (int(match.group(1)),)).fetchone()
                if row:
                    label = field(form, "label").strip() or row["label"]
                    url = (field(form, "url").strip().rstrip("/")
                           or row["url"])
                    key = field(form, "api_key").strip() or row["api_key"]
                    con.execute("UPDATE jellyfin_servers"
                                " SET label = ?, url = ?, api_key = ?"
                                " WHERE id = ?",
                                (label, url, key, row["id"]))
                    primary = con.execute(
                        "SELECT MIN(id) FROM jellyfin_servers"
                        " WHERE is_active = 1").fetchone()[0]
                    if primary == row["id"]:
                        # play, sessions and artwork read this pair
                        for name, value in (("jellyfin_url", url),
                                            ("jellyfin_api_key", key)):
                            con.execute(
                                "INSERT OR REPLACE INTO settings"
                                " (key, value) VALUES (?, ?)",
                                (name, value))
                    con.commit()
                return self.respond(303, "", extra={
                    "Location": (base or "/") + "admin#jellyfin"})

            if route.rstrip("/").endswith("/jellyfin/sync"):
                if jellyfin.start_sync(DB_PATH):
                    message = "Jellyfin sync started - the links refresh in the background."
                else:
                    message = "A Jellyfin sync is already running."
                return self.respond(303, "", extra={
                    "Location": base + "admin?jellyfin=#jellyfin" + quote(message)})
                return self.respond(303, "", extra={
                    "Location": base + "admin?jellyfin=#jellyfin" + quote(message)})

            match = re.search(r"/title/(\d+)/cover/?$", route)
            if match:
                title_id = int(match.group(1))
                blob = (form.get("image") or [b""])[0]
                if not isinstance(blob, bytes):
                    blob = b""
                ext, kind = image_ext(blob)
                if not ext:
                    return self.title_page(
                        con, viewer, base, title_id,
                        error="That file is not a JPEG, PNG, WebP or GIF "
                              "image.")
                if len(blob) > MAX_COVER_BYTES:
                    return self.title_page(
                        con, viewer, base, title_id,
                        error="That image is %.1f MB - the limit is %d MB."
                        % (len(blob) / 1048576.0,
                           MAX_COVER_BYTES // 1048576))
                try:
                    os.makedirs(COVERS_DIR, exist_ok=True)
                    name = "t%d-%d%s" % (title_id,
                                         int(time.time() * 1000), ext)
                    with open(os.path.join(COVERS_DIR, name), "wb") as out:
                        out.write(blob)
                except OSError as exc:
                    return self.title_page(
                        con, viewer, base, title_id,
                        error="Could not keep the image: %s" % exc)
                before = con.execute(
                    "SELECT poster_url FROM titles WHERE id = ?",
                    (title_id,)).fetchone()
                url = "/cover/" + name
                con.execute("UPDATE titles SET poster_url = ?,"
                            " art_source = 'poster',"
                            " metadata_updated_at = datetime('now')"
                            " WHERE id = ?", (url, title_id))
                metadata.remember_art(con, title_id, "upload", url)
                con.commit()
                if before and (before["poster_url"] or "") not in ("", url):
                    _forget_cover_file(before["poster_url"])
                return self.respond(303, "", extra={
                    "Location": base + "title/%d" % title_id})
            match = re.search(r"/title/(\d+)/art/?$", route)
            if match:
                title_id = int(match.group(1))
                action = field(form, "action") or "use"
                source = field(form, "source")
                url = field(form, "url").strip()
                if action == "auto":
                    # back to "Jellyfin first, then the poster", keeping the
                    # fetched poster as the fallback
                    con.execute("UPDATE titles SET art_source = NULL"
                                " WHERE id = ?", (title_id,))
                elif action == "forget" and url:
                    # drop it from the remembered candidates and, when it is the
                    # image on show, stop showing it
                    row = con.execute(
                        "SELECT art_candidates, poster_url FROM titles"
                        " WHERE id = ?", (title_id,)).fetchone()
                    try:
                        kept = [item for item in
                                json.loads(row["art_candidates"] or "[]")
                                if item.get("url") != url]
                    except ValueError:
                        kept = []
                    con.execute("UPDATE titles SET art_candidates = ?"
                                " WHERE id = ?", (json.dumps(kept), title_id))
                    if (row["poster_url"] or "") == url:
                        con.execute("UPDATE titles SET poster_url = NULL,"
                                    " art_source = NULL WHERE id = ?",
                                    (title_id,))
                    _forget_cover_file(url)
                elif source == "jellyfin":
                    con.execute("UPDATE titles SET art_source = 'jellyfin'"
                                " WHERE id = ?", (title_id,))
                elif source == "poster" and url.startswith(("http://",
                                                            "https://", "/")):
                    before_art = con.execute(
                        "SELECT poster_url FROM titles WHERE id = ?",
                        (title_id,)).fetchone()
                    con.execute("UPDATE titles SET poster_url = ?,"
                                " art_source = 'poster' WHERE id = ?",
                                (url, title_id))
                    metadata.remember_art(con, title_id, "chosen", url)
                    if before_art and before_art["poster_url"] != url:
                        _forget_cover_file(before_art["poster_url"])
                con.commit()
                return self.respond(303, "", extra={
                    "Location": base + "title/%d" % title_id})
            match = re.search(r"/title/(\d+)/sources/?$", route)
            if match:
                title_id = int(match.group(1))
                item_id = re.sub(r"\D", "", field(form, "item_id"))
                provider = field(form, "provider") or "rip"
                owns = con.execute("SELECT 1 FROM items WHERE id = ? AND title_id = ?",
                                   (item_id or 0, title_id)).fetchone()
                if not owns:
                    return self.title_page(con, viewer, base, title_id,
                                           error="Pick a copy of this title.")
                con.execute(
                    "INSERT INTO digital_sources (item_id, provider, library_id,"
                    " external_id, path, notes, root_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (int(item_id), provider, field(form, "library_id") or None,
                     field(form, "external_id") or None, field(form, "path") or None,
                     field(form, "notes") or None,
                     int(re.sub(r"\D", "", field(form, "root_id")) or 0) or None))
                con.commit()
                return self.title_page(con, viewer, base, title_id,
                                       summary="Playback source added.")

            match = re.search(r"/sources/(\d+)/delete/?$", route)
            if match:
                source = con.execute(
                    "SELECT d.id, d.provider, d.path, i.title_id FROM digital_sources d"
                    " JOIN items i ON i.id = d.item_id WHERE d.id = ?",
                    (int(match.group(1)),)).fetchone()
                # a copy backed by Jellyfin plays there, straight from its route
                jf = con.execute(
                    "SELECT d.external_id FROM digital_sources d"
                    " WHERE d.id = ? AND d.provider = 'jellyfin'",
                    (source_id,)).fetchone()
                if jf is not None:
                    try:
                        jellyfin.play(con, entity_id, jf["external_id"])
                    except jellyfin.JellyfinError as exc:
                        return self.title_page(con, viewer, base, title_id,
                                               error=str(exc))
                    return self.respond(303, "", extra={"Location":
                                        (base or "/") + "title/%d" % title_id})
                if source is None:
                    return self.respond(404, "No such source", "text/plain; charset=utf-8")
                con.execute("DELETE FROM digital_sources WHERE id = ?", (source["id"],))
                if source["provider"] == "rip":
                    scan.remember_skip(con, source["title_id"], source["path"])
                con.commit()
                return self.title_page(con, viewer, base, source["title_id"],
                                       summary="Playback source removed.")

            match = re.search(r"/items/(\d+)/watched/?$", route)
            if match:
                item_id = int(match.group(1))
                item = con.execute("SELECT title_id FROM items WHERE id = ?", (item_id,)).fetchone()
                if item is None:
                    return self.respond(404, "No such copy", "text/plain; charset=utf-8")
                con.execute("UPDATE items SET last_watched_on = date('now'),"
                            " updated_at = datetime('now') WHERE id = ?", (item_id,))
                con.commit()
                return self.title_page(con, viewer, base, item["title_id"],
                                       summary="Marked watched today.")

            match = re.search(r"/title/(\d+)/metadata/?$", route)
            if match:
                title_id = int(match.group(1))
                return self.title_page(con, viewer, base, title_id,
                                       summary=metadata.refresh(con, title_id))

            match = re.search(r"/title/(\d+)/play/?$", route)
            if match:
                title_id = int(match.group(1))
                entity_id = field(form, "entity_id")
                source_id = field(form, "source_id")
                row = con.execute("SELECT kind FROM titles WHERE id = ?",
                                  (title_id,)).fetchone()
                source = con.execute(
                    "SELECT d.path, r.path AS root_path FROM digital_sources d"
                    " LEFT JOIN media_roots r ON r.id = d.root_id WHERE d.id = ?",
                    (source_id,)).fetchone() if source_id.isdigit() else None
                if source is None:
                    return self.title_page(con, viewer, base, title_id,
                                           error="Pick a playback source first.")
                try:
                    message = playback.play(entity_id, source["root_path"],
                                            source["path"],
                                            row["kind"] if row else "movie")
                except playback.PlaybackError as exc:
                    return self.title_page(con, viewer, base, title_id, error=str(exc))
                playback.remember_player(con, entity_id)
                return self.title_page(con, viewer, base, title_id, summary=message)

            match = re.search(r"/title/(\d+)/store/?$", route)
            if match:
                title_id = int(match.group(1))
                row = stores.by_key(con).get(field(form, "store"))
                picked = field(form, "target")
                device = stores.target(con, int(picked) if picked.isdigit() else 0)
                if not (row and device and (device.get("ha_entity_id") or "").strip()):
                    return self.title_page(
                        con, viewer, base, title_id,
                        error="That store has no device to open it on - set one"
                              " in Admin, under Stores.")
                app = stores.link_app(con, row["id"], device["id"])
                if app is None:
                    return self.title_page(
                        con, viewer, base, title_id,
                        error="That player is not attached to %s - add it in"
                              " Admin, under Stores." % row["label"])
                try:
                    summary = playback.open_on(device, app)
                except Exception as exc:
                    return self.title_page(
                        con, viewer, base, title_id,
                        error="Could not wake %s: %s"
                              % (device.get("name") or row["label"], exc))
                return self.title_page(con, viewer, base, title_id,
                                       summary=summary)


            match = re.search(r"/title/(\d+)/jellyfin/?$", route)
            if match:
                title_id = int(match.group(1))
                link = jellyfin.link_for_title(con, title_id)
                try:
                    chosen = field(form, "source_id")
                    jf_id = None
                    ms_id = None
                    if chosen.isdigit():
                        # source_id is a digital_sources row, not an item: hop
                        # through it, or we hand Jellyfin an unrelated id
                        owner_item = con.execute(
                            "SELECT item_id, media_source_id"
                            " FROM digital_sources WHERE id = ?",
                            (int(chosen),)).fetchone()
                        if owner_item:
                            ms_id = owner_item["media_source_id"]
                            jf_id = con.execute(
                                "SELECT jf_id FROM jellyfin_links WHERE item_id = ?"
                                " ORDER BY jf_id LIMIT 1",
                                (owner_item["item_id"],)).fetchone()
                    kind_row = con.execute(
                        "SELECT kind FROM titles WHERE id = ?",
                        (title_id,)).fetchone()
                    summary = jellyfin.play(
                        con, field(form, "session_id"),
                        (jf_id["jf_id"] if jf_id else None)
                        or (link["jf_id"] if link else ""),
                        ms_id,
                        kind=kind_row["kind"] if kind_row else "")
                    return self.title_page(con, viewer, base, title_id, summary=summary)
                except jellyfin.JellyfinError as exc:
                    return self.title_page(con, viewer, base, title_id,
                                           error="Jellyfin: %s" % exc)

            match = re.search(r"/title/(\d+)/?$", route)
            if match:
                title_id = int(match.group(1))
                name = field(form, "name")
                if not name:
                    return self.title_page(con, viewer, base, title_id,
                                           error="Name is required.")
                year = re.sub(r"\D", "", field(form, "release_year"))
                tmdb = re.sub(r"\D", "", field(form, "tmdb_id"))
                kind = field(form, "kind") or "movie"
                new_imdb = (field(form, "imdb_id") or "").strip() or None
                before = con.execute(
                    "SELECT imdb_id, tmdb_id, release_year, kind,"
                    " musicbrainz_id FROM titles"
                    " WHERE id = ?", (title_id,)).fetchone()
                new_year = int(year) if len(year) == 4 else None
                new_tmdb = int(tmdb) if tmdb else None
                new_mb = (field(form, "musicbrainz_id") or "").strip() or None
                con.execute(
                    "UPDATE titles SET name = ?, release_year = ?, kind = ?,"
                    " musicbrainz_id = ? WHERE id = ?",
                    (name, new_year, kind, new_mb, title_id))
                if kind != "music":
                    con.execute(
                        "UPDATE titles SET imdb_id = ?, tmdb_id = ?"
                        " WHERE id = ?",
                        (new_imdb, new_tmdb, title_id))
                con.commit()
                if kind == "music":
                    # the form has an Artist box: honour it on an edit,
                    # not only when a title is first created
                    artists.link(con, title_id, field(form, "artist"),
                                 field(form, "artist_mbid"))
                    con.commit()

                message = "Title updated."
                changed = (new_imdb != before["imdb_id"]
                           or new_tmdb != before["tmdb_id"]
                           or new_year != before["release_year"]
                           or new_mb != before["musicbrainz_id"]
                           or kind != before["kind"])
                if changed:
                    # the stored plot/poster belong to whatever matched last time -
                    # drop them and re-resolve from the id/year you just entered
                    con.execute("UPDATE titles SET overview = NULL, poster_url = NULL,"
                                " metadata_source = NULL WHERE id = ?", (title_id,))
                    con.commit()
                    message += " " + metadata.refresh(con, title_id)
                return self.title_page(con, viewer, base, title_id, summary=message)

            match = re.search(r"/items/(\d+)/?$", route)
            if match:
                item_id = int(match.group(1))
                item = con.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
                if item is None:
                    return self.respond(404, "No such copy", "text/plain; charset=utf-8")
                fmt = field(form, "format")
                if fmt not in FORMATS:
                    fmt = item["format"]
                upc = re.sub(r"\D", "", field(form, "upc_ean")) or None
                if upc and len(upc) not in (12, 13):
                    return self.title_page(con, viewer, base, item["title_id"],
                                           error="Barcode must be 12 or 13 digits.")
                try:
                    con.execute(
                        "UPDATE items SET format = ?, edition = ?, upc_ean = ?,"
                        " region = ?, location = ?, condition = ?, visibility = ?,"
                        " last_watched_on = ?, price_paid = ?, acquired_on = ?,"
                        " notes = ?, updated_at = datetime('now') WHERE id = ?",
                        (fmt, field(form, "edition") or None, upc,
                         field(form, "region") or None,
                         field(form, "location") or None,
                         field(form, "condition") or None,
                         "private" if field(form, "visibility") == "private"
                         else "shared",
                         field(form, "last_watched_on") or None,
                         field(form, "price_paid") or None,
                         field(form, "acquired_on") or None,
                         field(form, "notes") or None, item_id))
                except sqlite3.IntegrityError:
                    con.rollback()
                    return self.title_page(con, viewer, base, item["title_id"],
                                           error="That barcode is already on another copy.")
                # One Store box, two places to keep it: a digital copy records
                # the shop on its source row, a disc records it on the copy.
                store = stores.resolve(con, field(form, "store"),
                                       "digital" if fmt == "digital" else "retail")
                # a purchase has one store; rips and Jellyfin keep their own
                con.execute("DELETE FROM digital_sources WHERE item_id = ?"
                            " AND provider NOT IN ('rip', 'jellyfin')",
                            (item_id,))
                if store and fmt == "digital":
                    con.execute("INSERT INTO digital_sources (item_id,"
                                " provider) VALUES (?, ?)",
                                (item_id, store))
                shop = stores.by_key(con).get(store) if store else None
                con.execute("UPDATE items SET store_id = ? WHERE id = ?",
                            (shop["id"] if shop and fmt != "digital" else None,
                             item_id))
                con.commit()
                return self.title_page(con, viewer, base, item["title_id"],
                                       summary="Copy updated.")

            match = re.search(r"/title/(\d+)/delete/?$", route)
            if match:
                doomed = int(match.group(1))
                copies = con.execute("SELECT id FROM items WHERE title_id = ?",
                                     (doomed,)).fetchall()
                for copy in copies:
                    con.execute("DELETE FROM digital_sources WHERE item_id = ?",
                                (copy[0],))
                    con.execute("DELETE FROM jellyfin_links WHERE item_id = ?",
                                (copy[0],))
                    con.execute("DELETE FROM items WHERE id = ?", (copy[0],))
                con.execute("DELETE FROM titles WHERE id = ?", (doomed,))
                con.commit()
                return self.respond(303, "", extra={
                    "Location": base_path(self)})
            match = re.search(r"/items/(\d+)/delete/?$", route)
            if match:
                row = con.execute(
                    "SELECT owner_id FROM items WHERE id = ?", (int(match.group(1)),)
                ).fetchone()
                if row is None:
                    return self.respond(404, "Not found", "text/plain; charset=utf-8")
                title_id = con.execute("SELECT title_id FROM items WHERE id = ?",
                                        (int(match.group(1)),)).fetchone()["title_id"]
                con.execute("DELETE FROM digital_sources WHERE item_id = ?",
                            (int(match.group(1)),))
                con.execute("DELETE FROM items WHERE id = ?", (int(match.group(1)),))
                con.execute("DELETE FROM jellyfin_links WHERE item_id = ?",
                            (int(match.group(1)),))
                # a Jellyfin copy is a shadow of a real one: once no real copy
                # is left on the title, the shadows go too, and then the title
                real = con.execute(
                    "SELECT COUNT(*) FROM items WHERE title_id = ?"
                    "   AND (format IS NULL OR format <> 'ripped'"
                    "        OR (rip_path IS NOT NULL"
                    "            AND TRIM(rip_path) <> ''))",
                    (title_id,)).fetchone()[0]
                if real == 0:
                    strays = [row[0] for row in con.execute(
                        "SELECT id FROM items WHERE title_id = ?",
                        (title_id,)).fetchall()]
                    for stray in strays:
                        con.execute("DELETE FROM digital_sources"
                                    " WHERE item_id = ?", (stray,))
                        con.execute("DELETE FROM jellyfin_links"
                                    " WHERE item_id = ?", (stray,))
                        con.execute("DELETE FROM items WHERE id = ?", (stray,))
                # a title with no copies left is not a title: it goes, with its file rows
                con.execute("DELETE FROM titles WHERE id = ? AND NOT EXISTS"
                            " (SELECT 1 FROM items WHERE title_id = ?)",
                            (title_id, title_id))
                con.commit()
                return self.respond(303, "", extra={"Location": base})

            if route.rstrip("/").endswith("/items"):
                with ADD_LOCK:
                    error, title_id, clash = add_item(con, viewer, form)
                if error:
                    return self.respond(400, render_index(con, viewer, base, error=error))
                if clash:
                    # 409 for a refused title, 200 for a question
                    return self.respond(
                        200 if clash["kind"] == "copy" else 409,
                        clash_page(base, form, clash))
                wants_metadata = bool(field(form, "fetch_metadata")) or bool(
                    (form.get("imdb_id") or [""])[0].strip())
                if title_id and wants_metadata:
                    con.execute("UPDATE titles SET overview = NULL, poster_url = NULL"
                                " WHERE id = ? AND overview IS NULL", (title_id,))
                    metadata.refresh(con, title_id)
                    return self.respond(303, "", extra={
                        "Location": base + "title/%d" % title_id})
                where = base
                if title_id and field(form, "from") == "title":
                    where = base + "title/%d?tab=copies" % title_id
                return self.respond(303, "", extra={"Location": where})

            return self.respond(404, "Not found", "text/plain; charset=utf-8")
        finally:
            con.close()


def _tls_pairs():
    """Every certificate/key pair this box could serve, best guess first."""
    pairs = []
    explicit = (os.environ.get("MC_TLS_CERT", ""),
                os.environ.get("MC_TLS_KEY", ""))
    if all(explicit) and os.path.exists(explicit[0]) and os.path.exists(
            explicit[1]):
        pairs.append(explicit)
    for cert, key in (("/ssl/fullchain.pem", "/ssl/privkey.pem"),
                      ("/data/tls/fullchain.pem", "/data/tls/privkey.pem")):
        if os.path.exists(cert) and os.path.exists(key):
            pairs.append((cert, key))
    # Nginx Proxy Manager keeps the certificates it renews here, one folder per
    # proxy host; /ssl/fullchain.pem is often an older copy left behind by an
    # earlier setup (on this box it expired in 2023), so it must not win by
    # being first.
    for pattern in ("/ssl/nginxproxymanager/letsencrypt/live/*/fullchain.pem",
                    "/ssl/nginxproxymanager/live/*/fullchain.pem"):
        for cert in sorted(glob.glob(pattern)):
            key = os.path.join(os.path.dirname(cert), "privkey.pem")
            if os.path.exists(key):
                pairs.append((cert, key))
    return pairs


def _cert_expiry(path):
    """Seconds since the epoch this certificate expires, or 0 if unreadable."""
    try:
        info = ssl._ssl._test_decode_cert(path)
        return ssl.cert_time_to_seconds(info["notAfter"])
    except Exception:
        return 0


def _tls_hosts(cert):
    """The DNS names a certificate claims, lowercased."""
    try:
        info = ssl._ssl._test_decode_cert(cert)
    except Exception:
        return []
    names = [value.lower() for kind, value in info.get("subjectAltName", ())
             if kind == "DNS"]
    for rdn in info.get("subject", ()):
        for field, value in rdn:
            if field == "commonName":
                names.append(value.lower())
    return names


def _tls_context():
    """A TLS context that serves the right certificate for the name asked for.

    The box can hold several: Home Assistant's own, Nginx Proxy Manager's per
    proxy host, and any dropped in /data/tls. Only unexpired ones are offered -
    serving an expired certificate is worse than serving plain HTTP, because
    the browser stops with a warning instead of the page - and the SNI name in
    the handshake picks the certificate whose subjectAltName matches, so
    https://home.<domain>:8099 gets the one browsers already trust for it.
    """
    now = time.time()
    pairs = [(cert, key) for cert, key in _tls_pairs()
             if _cert_expiry(cert) > now]
    if not pairs:
        seen = ", ".join(os.path.basename(os.path.dirname(cert)) or cert
                         for cert, _key in _tls_pairs())
        log("https: no unexpired certificate found (%s) - serving http only;"
            " point a proxy at this port or drop one in /data/tls"
            % (seen or "none"))
        return None
    by_name = {}
    for cert, key in sorted(pairs, key=lambda pair: _cert_expiry(pair[0])):
        for host in _tls_hosts(cert):
            by_name[host] = (cert, key)      # newest wins per name
    best = max(pairs, key=lambda pair: _cert_expiry(pair[0]))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(*best)

    def _switch(sock, name, ctx):
        pair = by_name.get((name or "").lower())
        if pair and pair != best:
            try:
                ctx.load_cert_chain(*pair)
            except Exception:
                pass

    context.set_servername_callback(_switch)
    return context


class DualServer(ThreadingHTTPServer):
    """One port, two protocols: HTTPS for the LAN, HTTP for the ingress.

    With no certificate configured this is the plain server. The peek costs one
    byte and only happens when TLS is set up, so a LAN https:// page and Home
    Assistant's own http:// proxy can share :8099 - ingress cannot be told to
    speak TLS.
    """

    def __init__(self, address, handler, ssl_context=None):
        self.ssl_context = ssl_context
        super().__init__(address, handler)

    def get_request(self):
        sock, addr = self.socket.accept()
        if self.ssl_context is None:
            return sock, addr
        sock.settimeout(2)
        try:
            first = sock.recv(1, socket.MSG_PEEK)
        except OSError:
            first = b""
        sock.settimeout(None)
        if first == b"\x16":               # a TLS ClientHello
            return self.ssl_context.wrap_socket(sock, server_side=True), addr
        return sock, addr


def _check_page():
    """Fail at boot, not with a blank page, if PAGE's Template has a stray `$`.

    `PAGE` is a `string.Template`: a regex end-anchor (`$/`) inside its script is
    an invalid placeholder, and the substitution error kills the request thread,
    so the catalogue answers 0 bytes with no traceback on the page. One
    substitute() at start-up turns that into a logged failure instead.
    """
    ids = set()
    for match in PAGE.pattern.finditer(PAGE.template):
        name = match.group("named") or match.group("braced")
        if name:
            ids.add(name)
    PAGE.substitute({name: "" for name in ids})


def main():
    if not os.path.exists(DB_PATH):
        raise SystemExit("database missing at %s - run migrate.sh first" % DB_PATH)
    _check_page()
    log("media collection listening on :%d (db=%s)" % (PORT, DB_PATH))
    scan.start(DB_PATH)
    jellyfin.start_sync(DB_PATH)
    try:
        streamed, dropped = dedupe.after_links(DB_PATH)
        if streamed or dropped:
            log("linkers: %d stream-only copy(s), %d empty copy(s) dropped"
                % (streamed, dropped))
    except Exception as exc:  # a tidy-up must never stop the shelf coming up
        log("linkers: tidy up skipped (%s)" % exc)
    artists.start(DB_PATH, limit=60)          # a few details per start
    # titles that arrived without metadata (an import, a bare add) get
    # looked up too; with nothing missing this is a single query
    refresh_job.start(DB_PATH, limit=60)
    # the same pass then repeats itself every day (or week), working through a
    # couple of hundred titles a night, so a shelf nobody touches still tidies
    # itself - and the artist fill rides along
    refresh_job.start_auto(DB_PATH, hours=refresh_hours(), limit=200,
                           also=lambda: artists.start(DB_PATH, limit=200))
    # Direct LAN access wants HTTPS - a barcode scanner is only handed the
    # camera on a secure origin - while Home Assistant's ingress proxy and the
    # watchdog still knock with plain HTTP on the same port, so the first byte
    # of each connection decides which one it is.
    context = _tls_context()
    if context:
        names = sorted({host for cert, _key in _tls_pairs()
                        for host in _tls_hosts(cert)})
        log("TLS on: :%d answers https (names: %s), http for the ingress"
            % (PORT, ", ".join(names[:4])))
    DualServer(("0.0.0.0", PORT), Handler, context).serve_forever()


if __name__ == "__main__":
    main()
