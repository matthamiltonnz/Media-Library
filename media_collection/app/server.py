#!/usr/bin/env python3
"""Media Collection - Home Assistant add-on.

Stdlib only (the base image ships python3 + sqlite3). Serves a server-rendered
page behind Home Assistant ingress; the HA user making the request becomes the
collection owner via the X-Remote-User-* headers the Supervisor forwards.
"""
import csv
import html
import io
import json
import os
import re
import sqlite3
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from string import Template
from urllib.parse import parse_qs, quote, urlparse

import dedupe
import importer
import jellyfin
import merger
import metadata
import playback
import refresh_job
import scan
import artists
import ui
import ui_artists
import ui_admin
import ui_import
import ui_title

PORT = int(os.environ.get("PORT", "8099"))
DB_PATH = os.environ.get("DB_PATH", "/data/media.db")
OPTIONS_PATH = "/data/options.json"

FORMATS = ["bluray_3d", "bluray", "uhd_4k", "dvd", "digital", "ripped", "cd", "vinyl", "sacd",
           "cassette", "vhs", "other"]
# A digital copy either came from one of these stores or is a ripped file.
PURCHASE_STORES = ("itunes", "appletv", "other")

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


CASE_LABEL = {CASE_CLASS["uhd_4k"]: "4K Ultra HD",
              CASE_CLASS["bluray_3d"]: "Blu-ray 3D",
              CASE_CLASS["bluray"]: "Blu-ray",
              CASE_CLASS["dvd"]: "DVD"}


def case_band(kind, formats):
    """The words printed across the top of the case, or '' for a case without one."""
    case = case_class(kind, formats)
    for name, label in CASE_LABEL.items():
        if name in case:
            return '<span class="band">%s</span>' % html.escape(label)
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


OPTIONS = load_options()
DEFAULT_VISIBILITY = OPTIONS.get("default_visibility", "shared")
LOG_LEVEL = OPTIONS.get("log_level", "info")
RIPPED_ROOT = OPTIONS.get("ripped_movies_path", "/media/My Media/QNAP/Movies")


def log(msg):
    if not msg.startswith("debug:") or LOG_LEVEL == "debug":
        print(msg, flush=True)


def connect():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


# ------------------------------------------------------------------ users ----

def ensure_user(con, username, display_name):
    """Create the row on first sight, so a new HA user needs no admin step."""
    con.execute(
        "INSERT OR IGNORE INTO users (username, display_name) VALUES (?, ?)",
        (username, display_name),
    )
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

def library_rows(con, viewer_id, query="", kind="", owner=None, format="",
                 rips=False):
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
    if not rips:
        sql += " AND NOT " + RIP_ONLY
    sql += " ORDER BY t.name COLLATE NOCASE, i.format"
    return con.execute(sql, params).fetchall()


# A title whose only copies are files on the NAS: hidden by default so the shelf is
# about discs, one click away when browsing what can actually be played.
RIP_ONLY = ("NOT EXISTS (SELECT 1 FROM items o WHERE o.title_id = t.id"
            " AND NOT EXISTS (SELECT 1 FROM digital_sources r"
            "   WHERE r.item_id = o.id AND r.provider = 'rip'))")


def visible_clause(query="", kind="", owner=None, format="",
                   rips=False):
    where = ["(i.visibility = 'shared' OR i.owner_id = ?)"]
    params = []                              # viewer id is supplied by the caller
    if query:
        where.append("(t.name LIKE ? OR COALESCE(i.upc_ean, '') = ?)")
        params += ["%" + query + "%", query]
    if kind:
        where.append("t.kind = ?")
        params.append(kind)
    if owner:
        where.append("i.owner_id = ?")
        params.append(int(owner))
    else:
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
    return " AND ".join(where), params


def library_stats(con, viewer_id, query="", kind="", owner=None,
                  format="", rips=False):
    clause, extra = visible_clause(query, kind, owner, format, rips)
    if not rips:
        clause += " AND NOT " + RIP_ONLY
    params = [viewer_id] + extra
    base = ("FROM items i JOIN titles t ON t.id = i.title_id WHERE " + clause)
    titles = con.execute("SELECT COUNT(DISTINCT t.id) " + base, params).fetchone()[0]
    copies = con.execute("SELECT COUNT(*) " + base, params).fetchone()[0]
    # a copy that is a file on the NAS counts as "ripped", not as the format of
    # whatever disc it came from, so the tally matches what is on the shelf
    formats = con.execute(
        "SELECT CASE WHEN EXISTS (SELECT 1 FROM digital_sources r"
        "                    WHERE r.item_id = i.id AND r.provider = 'rip')"
        " THEN 'rip' ELSE i.format END AS fmt, COUNT(*) AS n " + base +
        " GROUP BY fmt ORDER BY n DESC", params).fetchall()
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
  form.tally { display: flex; flex-wrap: wrap; gap: .4rem; align-items: center;
               margin: 0 0 1rem; }
  .tallybox { display: inline-flex; gap: .35rem; align-items: center;
              border: 1px solid #8886; border-radius: 6px;
              padding: .15rem .45rem; font-size: .8rem; cursor: pointer; }
  .tallybox input { margin: 0; }
  .tallybox .n { opacity: .6; }
  h2 { font-size: 1rem; margin: 1.6rem 0 .6rem; }
  .sub { opacity: .7; margin: 0 0 1rem; }
  table { border-collapse: collapse; width: 100%; }
  th, td { text-align: left; padding: .35rem .5rem; border-bottom: 1px solid #8883; }
  th { font-size: .78rem; text-transform: uppercase; letter-spacing: .03em; opacity: .65; }
  .tag { font-size: .72rem; border: 1px solid #8886; border-radius: 999px;
         padding: 0 .45rem; white-space: nowrap; }
  .barcode { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .82rem; }
  form.add { display: grid; gap: .5rem;
             grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
             max-width: 46rem; }
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
  .thumb.case-digital { background: #8881; }
  .card .cap { padding: .4rem .5rem; font-size: .82rem; display: grid; gap: .15rem; }
  .card .cap .muted { font-size: .74rem; }
  label.check { display: flex; align-items: center; gap: .4rem; font-size: .85rem; }
  label.check input { width: auto; }
  nav.modes { display: flex; gap: .4rem; margin: 0 0 .7rem; flex-wrap: wrap; }
  nav.modes a { padding: .38rem .85rem; border-radius: 999px; border: 1px solid #8886;
                color: inherit; text-decoration: none; font-weight: 600;
                font-size: .9rem; }
  nav.modes a.current { background: #2f6f4f; border-color: #2f6f4f; color: #fff; }
  nav.modes .n { opacity: .7; font-weight: 400; margin-left: .3rem; font-size: .82rem; }
  nav.owners { margin-top: -.35rem; }
  nav.owners a { font-size: .8rem; padding: .22rem .6rem; font-weight: 500; }
  nav.owners .n { font-size: .76rem; }
  .banner { padding: .5rem .7rem; border-radius: 6px; background: #c33;
            color: #fff; margin-bottom: 1rem; }
  .empty { opacity: .6; }
  dialog { border: 1px solid #8886; border-radius: 10px; padding: 1rem 1.2rem;
           max-width: 32rem; width: calc(100% - 2rem); }
  dialog::backdrop { background: #0008; }
  dialog h2 { margin-top: 0; }
  fieldset.music { border: 1px solid #8884; border-radius: 8px;
                   padding: .5rem .7rem; margin: 0 0 .2rem;
                   display: grid; gap: .5rem;
                   grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr)); }
  fieldset.music legend { font-size: .78rem; text-transform: uppercase;
                          letter-spacing: .04em; opacity: .65; }
</style>
</head>
<body>
<h1>Media Collection<a class="admin" href="${base}admin">admin</a></h1>
<p class="sub">Signed in as <strong>$viewer</strong> — you see everything shared
   plus your own private copies.</p>
<p class="sub"><button type="button" onclick="addcopy.showModal()">+ Add a copy</button></p>
$modes
<nav class="modes owners">$owner_tabs</nav>
<form class="search" method="get" action="$base">
  <input name="q" value="$q" placeholder="Search title or barcode">
  <input type="hidden" name="kind" value="$kind">
  <input type="hidden" name="owner" value="$owner">
  <input type="hidden" name="view" value="$view">
  $format_hidden
  <button type="submit">Search</button>
</form>
<p class="sub">$viewtoggle$clear</p>
$notice
$error
<h2>$heading$scope</h2>
<div class="sub">$totals</div>
$rips_toggle
$library

<dialog id="addcopy">
<h2>Add a copy</h2>
<form class="add" method="post" action="${base}items">
  <input type="hidden" name="kind" value="$kind">
  <input name="title" placeholder="Title" required>
  <input name="release_year" placeholder="Year" inputmode="numeric" pattern="[0-9]{0,4}">
  <select name="format">$format_options</select>
  <select name="owner_id">$owner_options</select>
  <select name="store">$store_options</select>
  <input name="edition" placeholder="Edition (optional)">
  <input name="upc_ean" placeholder="UPC / EAN (optional)" inputmode="numeric">
  <input name="imdb_id" placeholder="IMDb ID (optional, tt…)">
  <input name="location" placeholder="Where it lives (optional)">
  <select name="visibility">
    <option value="shared">Shared with family</option>
    <option value="private">Private to me</option>
  </select>
  <label class="check"><input type="checkbox" name="fetch_metadata" value="1">
    Look up metadata</label>
  <fieldset class="music">
    <legend>Music</legend>
    <input name="artist" placeholder="Artist (albums only)">
    <input name="artist_mbid" placeholder="Artist MusicBrainz id (optional)">
    <input name="musicbrainz_id" placeholder="Release MusicBrainz id (optional)">
  </fieldset>
  <button type="submit">Add</button>
  <button type="button" onclick="addcopy.close()">Cancel</button>
</form>
</dialog>

</body>
</html>
""")

ROW = Template("""<tr>
  <td><a href="${base}title/$title_id">$title</a></td>
  <td>$year</td>
  <td><span class="tag">$format</span></td>
  <td>$owner$private</td>
  <td class="barcode">$barcode</td>
  <td>$watched</td>
  <td>$location</td>
  <td>$delete</td>
</tr>""")


def render_index(con, viewer, base, error=None, query="", pending=0, merged=0,
                 view="list", kind="", owner=None, format="", rips=False):
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
    bits_rips = [bit for bit in bits if not bit.startswith("rips=")]
    if not rips:
        bits_rips.append("rips=1")
    rips_link = base + ("?" + "&".join(bits_rips) if bits_rips else "")
    rips_toggle = ('<p class="sub"><a href="%s">%s</a></p>'
                   % (html.escape(rips_link),
                      "Hide titles that are only ripped files" if rips
                      else "Show titles that are only ripped files"))

    rows = library_rows(con, viewer["id"], query, kind, owner, format,
                          rips)
    titles, copies, formats = library_stats(con, viewer["id"], query,
                                            kind, owner, format, rips)
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

    # one box per format: ticking several adds several ?format= values, so the
    # shelf can be narrowed to "Blu-ray or 4K" instead of one format at a time
    carry = "".join(hidden(name, value) for name, value in (
        ("q", query), ("kind", kind), ("owner", owner),
        ("view", view if view != "list" else ""),
        ("rips", 1 if rips else "")) if value not in ("", None, 0))
    boxes = "".join(
        '<label class="tallybox"><input type="checkbox" name="format"'
        ' value="%s"%s onchange="this.form.submit()"> %s'
        '<span class="n">%d</span></label>'
        % (html.escape(row["fmt"]),
           " checked" if row["fmt"] in picked else "",
           html.escape(FORMAT_LABELS.get(row["fmt"], row["fmt"])),
           row["n"])
        for row in formats)
    totals = ('<form class="tally" method="get" action="%s">%s%s'
              '<button type="submit">Apply</button>%s</form>'
              % (html.escape(base), carry,
                 boxes or '<span class="muted">nothing yet</span>',
                 ' <a href="%s">clear</a>' % html.escape(tally_link(""))
                 if picked else ""))
    mode_counts = {row["kind"]: row["n"] for row in con.execute(
        "SELECT t.kind AS kind, COUNT(*) AS n FROM items i"
        " JOIN titles t ON t.id = i.title_id"
        " WHERE (i.visibility = 'shared' OR i.owner_id = ?) GROUP BY t.kind",
        (viewer["id"],))}
    modes = "".join(
        '<a class="%s" href="%s?kind=%s%s">%s<span class="n">%d</span></a>'
        % ("current" if value == kind else "", html.escape(base), value,
           "&amp;view=grid" if view == "grid" else "", label,
           mode_counts.get(value, 0))
        for value, label in (MODES + (("other", "Other"),)
                             if mode_counts.get("other") else MODES))
    # every household member appears, even with nothing in this mode, so a shared
    # library never disappears from the filter
    owner_rows = con.execute(
        "SELECT u.id, u.display_name FROM users u WHERE EXISTS ("
        "  SELECT 1 FROM items i WHERE i.owner_id = u.id"
        "  AND (i.visibility = 'shared' OR i.owner_id = ?))"
        " ORDER BY u.display_name COLLATE NOCASE", (viewer["id"],)).fetchall()
    owner_counts = {row["owner_id"]: row["n"] for row in con.execute(
        "SELECT i.owner_id AS owner_id, COUNT(*) AS n FROM items i"
        " JOIN titles t ON t.id = i.title_id"
        " WHERE (i.visibility = 'shared' OR i.owner_id = ?)%s"
        " GROUP BY i.owner_id" % (" AND t.kind = ?" if kind else ""),
        [viewer["id"]] + ([kind] if kind else []))}
    owner_tabs = "".join(
        ['<a class="%s" href="%s?kind=%s%s">Everyone<span class="n">%d</span></a>'
         % ("" if owner else "current", html.escape(base), kind,
            "&amp;view=grid" if view == "grid" else "",
            sum(owner_counts.values()))]
        + ['<a class="%s" href="%s?kind=%s&amp;owner=%d%s">%s<span class="n">%d</span></a>'
           % ("current" if owner == row["id"] else "", html.escape(base), kind,
              row["id"], "&amp;view=grid" if view == "grid" else "",
              html.escape(row["display_name"]), owner_counts.get(row["id"], 0))
           for row in owner_rows])
    if merged:
        notice = ('<p class="banner good">Merged %d item(s) from the bundled catalogue — '
                  "reload to see everything.</p>" % merged)
    elif pending:
        notice = ('<form method="post" action="%smerge"><p class="banner">%d item(s) in the '
                  'bundled catalogue are not in this library. '
                  '<button type="submit">Merge them</button></p></form>' % (base, pending))
    else:
        notice = ""
    if view == "grid":
        clause, extra = visible_clause(query, kind, owner, format,
                                       rips)
        cards = con.execute(
            "SELECT t.id, t.name, t.release_year, t.poster_url, t.kind,"
            " COALESCE(t.art_source, '') AS art_source,"
            " (SELECT l.jf_id FROM jellyfin_links l"
            "   JOIN items li ON li.id = l.item_id"
            "   WHERE li.title_id = t.id ORDER BY l.jf_kind LIMIT 1) AS jf_id,"
            " COUNT(i.id) AS copies,"
            # every format the title holds, not just the ones being filtered
            " (SELECT group_concat(DISTINCT ai.format) FROM items ai"
            "   WHERE ai.title_id = t.id"
            "     AND (ai.visibility = 'shared' OR ai.owner_id = ?)) AS formats"
            " FROM titles t JOIN items i ON i.title_id = t.id"
            " WHERE " + clause +
            " GROUP BY t.id ORDER BY t.name COLLATE NOCASE",
            [viewer["id"], viewer["id"]] + extra).fetchall()
        library = '<div class="cards">%s</div>' % ("".join(
            '<a class="card" href="{0}title/{1}?from={5}">{2}'
            '<div class="cap"><strong>{3}</strong><span class="muted">{4}</span></div></a>'
            .format(
                base, card["id"],
                '<div class="thumb %s">%s%s</div>' % (
                    case_class(card["kind"], card["formats"]),
                    case_band(card["kind"], card["formats"]),
                    '<img src="%s" alt="" loading="lazy">' % html.escape(
                        ui.pick_cover(base, card["jf_id"], card["poster_url"],
                                      card["art_source"]))
                    if (card["jf_id"] or card["poster_url"])
                    else '<span class="muted">no image</span>'),
                html.escape(card["name"]),
                html.escape(" · ".join(filter(None, [
                    str(card["release_year"] or ""),
                    " · ".join(FORMAT_LABELS.get(f, f)
                               for f in (card["formats"] or "").split(",")),
                    ])))
                , html.escape(here)
            )
            for card in cards) or '<p class="empty">Nothing yet — add your first disc.</p>')
    else:
        body = "".join(
            ROW.substitute(
                base=base,
                title_id=r["title_id"],
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
        ) or '<tr><td colspan="8" class="empty">Nothing yet — add your first disc.</td></tr>'
        library = ("<table><thead><tr><th>Title</th><th>Year</th><th>Format</th>"
                   "<th>Owner</th><th>Barcode</th><th>Watched</th><th>Where</th>"
                   "<th></th></tr></thead><tbody>%s</tbody></table>" % body)
    return PAGE.substitute(
        base=base,
        viewer=html.escape(viewer["display_name"]),
        error='<p class="banner">%s</p>' % html.escape(error) if error else "",
        notice=notice,
        count=len(rows),
        heading=heading,
        totals=totals,
        format_hidden="".join(
            '<input type="hidden" name="format" value="%s">' % html.escape(f)
            for f in picked),
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
        rips_toggle=rips_toggle,
        viewtoggle=(' · <a href="%s?view=%s&amp;kind=%s%s%s%s">%s</a>'
                    % (html.escape(base), "list" if view == "grid" else "grid",
                       html.escape(kind),
                       "&amp;q=" + html.escape(query) if query else "",
                       "&amp;owner=%d" % owner if owner else "",
                       "&amp;format=" + html.escape(format) if format else "",
                       "List view" if view == "grid" else "Thumbnails")
                    + (' · <a href="%sartists">By artist</a>'
                       % html.escape(base) if kind == "music" else "")),
        library=library,
        format_options="".join(
            '<option value="{0}">{1}</option>'.format(f, FORMAT_LABELS[f]) for f in FORMATS
        ),
        owner_options="".join(
            '<option value="{0}"{2}>{1}</option>'.format(
                row["id"], html.escape(row["display_name"]),
                " selected" if row["id"] == viewer["id"] else "")
            for row in owner_rows),
        store_options="".join(
            '<option value="{0}">{1}</option>'.format(s, ui_title.STORE_LABELS[s])
            for s in PURCHASE_STORES),
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


def add_item(con, viewer, form):
    """Insert title (reused when it already exists) + item.

    Returns (error, title_id): error is a message for the user, title_id the row
    that was added to (used to jump to the detail page / fetch metadata).
    """
    title = field(form, "title")
    if not title:
        return "Title is required.", None

    kind = field(form, "kind")
    if kind not in KINDS:
        kind = "movie"

    year_raw = field(form, "release_year")
    year = int(year_raw) if year_raw.isdigit() else None

    fmt = field(form, "format") or "bluray"
    if fmt not in FORMATS:
        return "Unknown format.", None

    upc = re.sub(r"\D", "", field(form, "upc_ean")) or None
    if upc and len(upc) not in (12, 13):
        return "UPC/EAN must be 12 or 13 digits.", None

    visibility = field(form, "visibility") or DEFAULT_VISIBILITY
    if visibility not in ("shared", "private"):
        visibility = "shared"

    imdb = (field(form, "imdb_id") or "").strip() or None
    existing = con.execute(
        "SELECT id, imdb_id FROM titles WHERE lower(name) = lower(?) "
        "AND COALESCE(release_year, -1) = COALESCE(?, -1)",
        (title, year),
    ).fetchone()
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
        # an album can be filed under its artist and MusicBrainz release here
        release_mbid = (field(form, "musicbrainz_id") or "").strip()
        if release_mbid:
            con.execute("UPDATE titles SET musicbrainz_id = ? WHERE id = ?",
                        (release_mbid, title_id))
        artists.link(con, title_id, field(form, "artist"),
                     field(form, "artist_mbid"))

    try:
            owner_id = (re.sub(r"\D", "", field(form, "owner_id"))
                        or viewer["id"])
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
            store = field(form, "store")
            if fmt == "digital" and store in PURCHASE_STORES:
                con.execute("INSERT INTO digital_sources (item_id, provider)"
                            " VALUES (?, ?)", (cursor.lastrowid, store))
    except sqlite3.IntegrityError as exc:
        con.rollback()
        if "upc_ean" in str(exc):
            return "That barcode is already in the collection.", None
        return "Could not save: %s" % exc, None
    con.commit()
    return None, title_id


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
        fields.setdefault(name, []).append(data.rstrip(b"\r\n").decode("utf-8", "replace"))
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
        " ORDER BY t.name COLLATE NOCASE, i.format", (viewer_id,)).fetchall()


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
            if route.rstrip("/").endswith("/health"):
                return self.respond(200, json.dumps({"status": "ok"}), "application/json")
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
                merged = (params.get("merged") or ["0"])[0]
                deduped = (params.get("deduped") or ["0"])[0]
                message = None
                if merged.isdigit() and int(merged):
                    message = "Merged %d item(s) from the bundled catalogue." % int(merged)
                elif deduped.isdigit() and int(deduped):
                    message = "Folded %d duplicate title(s)." % int(deduped)
                elif params.get("jellyfin"):
                    message = params["jellyfin"][0]
                elif params.get("players"):
                    message = params["players"][0]
                elif params.get("artists"):
                    message = params["artists"][0]
                elif params.get("scan") and scan.summary():
                    message = scan.summary()
                return self.respond(200, ui_admin.render(
                    base, viewer, stats(con), merger.pending_count(con), message,
                    refresh_job.status(), len(dedupe.groups(con)),
                    con.execute("SELECT * FROM media_roots ORDER BY medium, label").fetchall(),
                    jellyfin.status(con), scan.summary(),
                    players=playback.players(),
                    chosen=playback.chosen(con)))

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

            if route.rstrip("/").endswith("/import"):
                return self.respond(200, ui_import.upload_page(base))

            match = re.search(r"/import/(\d+)/map/?$", route)
            if match:
                imp = con.execute("SELECT * FROM imports WHERE id = ?",
                                  (int(match.group(1)),)).fetchone()
                if imp is None:
                    return self.respond(404, "No such import", "text/plain; charset=utf-8")
                headers, _ = importer.parse_csv(imp["raw_csv"])
                mapping = json.loads(imp["mapping_json"]) if imp["mapping_json"] else {}
                rows = con.execute("SELECT * FROM import_rows WHERE import_id = ?"
                                   " ORDER BY line_no", (imp["id"],)).fetchall()
                counts = import_counts(con, imp["id"])
                return self.respond(200, ui_import.mapping_page(
                    base, imp, headers, mapping,
                    rows=rows or None, counts=counts or None))

            query = (parse_qs(urlparse(self.path).query).get("q") or [""])[0].strip()
            view = (parse_qs(urlparse(self.path).query).get("view") or ["grid"])[0]
            kind = (parse_qs(urlparse(self.path).query).get("kind") or [""])[0]
            owner_raw = (parse_qs(urlparse(self.path).query).get("owner") or [""])[0]
            owner = int(owner_raw) if owner_raw.isdigit() else None
            fmt = ",".join(f for f in parse_qs(urlparse(self.path).query)
                           .get("format", []) if f)
            rips = bool(parse_qs(urlparse(self.path).query).get("rips"))
            merged = (parse_qs(urlparse(self.path).query).get("merged") or ["0"])[0]
            if route.rstrip("/").endswith("/artists"):
                want = (parse_qs(urlparse(self.path).query).get("view")
                        or ["list"])[0]
                return self.respond(200, ui_artists.render_list(
                    base, artists.all_artists(con, viewer["id"]),
                    "grid" if want == "grid" else "list"))

            match = re.search(r"/artist/(\d+)/?$", route)
            if match:
                artist = artists.get(con, int(match.group(1)))
                if artist is None:
                    return self.respond(404, "No such artist",
                                        "text/plain; charset=utf-8")
                want = (parse_qs(urlparse(self.path).query).get("view")
                        or ["list"])[0]
                return self.respond(200, ui_artists.render_artist(
                    base, artist,
                    artists.titles_for(con, artist["id"], viewer["id"]),
                    FORMAT_LABELS, "grid" if want == "grid" else "list"))

            # detail page, always last so /title/<id>/metadata is handled in POST
            detail = re.search(r"/title/(\d+)/?$", route)
            if detail:
                return self.title_page(con, viewer, base, int(detail.group(1)),
                                       owner=owner)
            return self.respond(200, render_index(
                con, viewer, base, query=query,
                pending=merger.pending_count(con),
                merged=int(merged) if merged.isdigit() else 0,
                view="grid" if view == "grid" else "list",
                owner=owner,
                kind=kind if kind in ("movie", "tv", "music", "other") else "movie",
                format=fmt, rips=rips))
        finally:
            con.close()

    def title_page(self, con, viewer, base, title_id, summary=None, error=None,
                   owner=None):
        row = con.execute("SELECT * FROM titles WHERE id = ?", (title_id,)).fetchone()
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
            " r.label AS root_label, r.path AS root_path"
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
        back = source if source.startswith("?") else None
        return self.respond(200, ui_title.render(
            base, row, items, digital, FORMATS, FORMAT_LABELS, summary=summary,
            error=error, ripped_root=RIPPED_ROOT, roots=roots, owner=owner,
            players=playback.players_for(con), last_player=playback.last_player(con),
            jellyfin_link=jellyfin_link, jellyfin_sessions=jellyfin_sessions,
            jellyfin_configured=bool(jellyfin.config(con)[1]),
            users=con.execute("SELECT id, display_name FROM users"
                              " ORDER BY display_name").fetchall(),
            viewer_id=viewer["id"], tab=tab, back=back))

    def do_POST(self):
        form = parse_body(self)
        con = connect()
        try:
            viewer = viewer_for(self, con)
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
                return self.respond(303, "", extra={"Location": base + "admin"})

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
                return self.respond(303, "", extra={"Location": base + "admin"})

            match = re.search(r"/admin/roots/(\d+)/delete/?$", route)
            if match:
                con.execute("UPDATE digital_sources SET root_id = NULL WHERE root_id = ?",
                            (int(match.group(1)),))
                con.execute("DELETE FROM media_roots WHERE id = ?", (int(match.group(1)),))
                con.commit()
                return self.respond(303, "", extra={"Location": base + "admin"})

            if route.rstrip("/").endswith("/admin/dedupe"):
                folded = dedupe.merge(con)
                return self.respond(303, "", extra={
                    "Location": base + "admin?deduped=%d" % folded})

            if route.rstrip("/").endswith("/admin/refresh"):
                refresh_job.start(DB_PATH, force=bool(field(form, "force")))
                return self.respond(303, "", extra={"Location": base + "admin"})

            if route.rstrip("/").endswith("/admin/scan"):
                scan.start(DB_PATH, repair=True)
                return self.respond(303, "", extra={"Location": base + "admin?scan=1"})

            if route.rstrip("/").endswith("/jellyfin/save"):
                url = field(form, "jellyfin_url").strip()
                key = field(form, "jellyfin_api_key").strip()
                for name, value in (("jellyfin_url", url), ("jellyfin_api_key", key)):
                    if value:
                        con.execute("INSERT OR REPLACE INTO settings (key, value)"
                                    " VALUES (?, ?)", (name, value))
                con.commit()
                return self.respond(303, "", extra={
                    "Location": base + "admin?jellyfin="
                    + quote("Jellyfin settings saved - press Sync to match the copies.")})

            if route.rstrip("/").endswith("/admin/artists"):
                artists.start(DB_PATH)
                return self.respond(303, "", extra={
                    "Location": base + "admin?artists="
                    + quote("Filling artist details from MusicBrainz.")})

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
                    "Location": base + "admin?players="
                    + quote("Player list saved.")})

            if route.rstrip("/").endswith("/jellyfin/sync"):
                if jellyfin.start_sync(DB_PATH):
                    message = "Jellyfin sync started - the links refresh in the background."
                else:
                    message = "A Jellyfin sync is already running."
                return self.respond(303, "", extra={
                    "Location": base + "admin?jellyfin=" + quote(message)})
                return self.respond(303, "", extra={
                    "Location": base + "admin?jellyfin=" + quote(message)})

            if route.rstrip("/").endswith("/merge"):
                result = merger.merge(con)
                added = result["items"] if result else 0
                return self.respond(303, "", extra={
                    "Location": base + "?merged=%d" % added})

            match = re.search(r"/title/(\d+)/art/?$", route)
            if match:
                title_id = int(match.group(1))
                source = field(form, "source")
                url = field(form, "url").strip()
                if source == "jellyfin":
                    con.execute("UPDATE titles SET art_source = 'jellyfin'"
                                " WHERE id = ?", (title_id,))
                elif source == "poster" and url.startswith(("http://", "https://", "/")):
                    con.execute("UPDATE titles SET poster_url = ?,"
                                " art_source = 'poster' WHERE id = ?",
                                (url, title_id))
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

            match = re.search(r"/title/(\d+)/jellyfin/?$", route)
            if match:
                title_id = int(match.group(1))
                link = jellyfin.link_for_title(con, title_id)
                try:
                    summary = jellyfin.play(con, field(form, "session_id"),
                                            link["jf_id"] if link else "")
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
                    "SELECT imdb_id, tmdb_id, release_year, kind FROM titles"
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

                message = "Title updated."
                changed = (new_imdb != before["imdb_id"]
                           or new_tmdb != before["tmdb_id"]
                           or new_year != before["release_year"]
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
                store = field(form, "store")
                if store in PURCHASE_STORES or not store:
                    # a purchase has one store; rips and Jellyfin keep theirs
                    con.execute("DELETE FROM digital_sources WHERE item_id = ?"
                                " AND provider NOT IN ('rip', 'jellyfin')",
                                (item_id,))
                    if store in PURCHASE_STORES:
                        con.execute("INSERT INTO digital_sources (item_id,"
                                    " provider) VALUES (?, ?)",
                                    (item_id, store))
                con.commit()
                return self.title_page(con, viewer, base, item["title_id"],
                                       summary="Copy updated.")

            match = re.search(r"/items/(\d+)/delete/?$", route)
            if match:
                row = con.execute(
                    "SELECT owner_id FROM items WHERE id = ?", (int(match.group(1)),)
                ).fetchone()
                if row is None:
                    return self.respond(404, "Not found", "text/plain; charset=utf-8")
                if row["owner_id"] != viewer["id"]:
                    return self.respond(403, "Only the owner can remove a copy.",
                                        "text/plain; charset=utf-8")
                con.execute("DELETE FROM items WHERE id = ?", (int(match.group(1)),))
                con.commit()
                return self.respond(303, "", extra={"Location": base})

            if route.rstrip("/").endswith("/import"):
                text = (field(form, "paste") or "").strip()
                filename = None
                upload = (form.get("csv") or [""])[0]
                if not text and upload.strip():
                    text = upload
                    filename = "uploaded.csv"
                if not text:
                    return self.respond(200, ui_import.upload_page(
                        base, error="No CSV supplied — choose a file or paste the text."))
                cur = con.execute(
                    "INSERT INTO imports (filename, raw_csv, created_by) VALUES (?, ?, ?)",
                    (filename, text, viewer["id"]))
                con.commit()
                return self.respond(303, "", extra={
                    "Location": base + "import/%d/map" % cur.lastrowid})

            match = re.search(r"/import/(\d+)/map/?$", route)
            if match:
                imp = con.execute("SELECT * FROM imports WHERE id = ?",
                                  (int(match.group(1)),)).fetchone()
                if imp is None:
                    return self.respond(404, "No such import", "text/plain; charset=utf-8")
                mapping = {}
                for field_name, _label in importer.FIELDS:
                    header = (form.get("map_" + field_name) or [""])[0].strip()
                    if header:
                        mapping[field_name] = header
                importer.stage(con, imp, mapping)
                return self.respond(303, "", extra={
                    "Location": base + "import/%d/map" % imp["id"]})

            match = re.search(r"/import/(\d+)/commit/?$", route)
            if match:
                imp = con.execute("SELECT * FROM imports WHERE id = ?",
                                  (int(match.group(1)),)).fetchone()
                if imp is None:
                    return self.respond(404, "No such import", "text/plain; charset=utf-8")
                result = importer.commit(con, imp, viewer["id"], DEFAULT_VISIBILITY)
                if result is None:
                    summary = {"ok": False, "text": "This import was already committed."}
                else:
                    imported, failed = result
                    text = "%d rows imported" % imported
                    if failed:
                        text += " · %d could not be imported" % failed
                    summary = {"ok": imported > 0, "text": text}
                imp = con.execute("SELECT * FROM imports WHERE id = ?", (imp["id"],)).fetchone()
                headers, _ = importer.parse_csv(imp["raw_csv"])
                mapping = json.loads(imp["mapping_json"]) if imp["mapping_json"] else {}
                rows = con.execute("SELECT * FROM import_rows WHERE import_id = ?"
                                   " ORDER BY line_no", (imp["id"],)).fetchall()
                return self.respond(200, ui_import.mapping_page(
                    base, imp, headers, mapping, rows=rows or None,
                    counts=import_counts(con, imp["id"]) or None, summary=summary))

            if route.rstrip("/").endswith("/items"):
                error, title_id = add_item(con, viewer, form)
                if error:
                    return self.respond(400, render_index(con, viewer, base, error=error))
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


def main():
    if not os.path.exists(DB_PATH):
        raise SystemExit("database missing at %s - run migrate.sh first" % DB_PATH)
    log("media collection listening on :%d (db=%s)" % (PORT, DB_PATH))
    scan.start(DB_PATH)
    jellyfin.start_sync(DB_PATH)
    artists.start(DB_PATH, limit=60)          # a few details per start
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
