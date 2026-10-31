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
import merger
import metadata
import refresh_job
import ui_admin
import ui_import
import ui_title

PORT = int(os.environ.get("PORT", "8099"))
DB_PATH = os.environ.get("DB_PATH", "/data/media.db")
OPTIONS_PATH = "/data/options.json"

FORMATS = ["bluray_3d", "bluray", "uhd_4k", "dvd", "digital", "cd", "vinyl", "sacd",
           "cassette", "vhs", "other"]
FORMAT_LABELS = {
    "bluray_3d": "3D Blu-ray",
    "bluray": "Blu-ray",
    "uhd_4k": "4K Blu-ray",
    "dvd": "DVD",
    "digital": "Digital",
    "cd": "CD",
    "vinyl": "Vinyl",
    "sacd": "SACD",
    "cassette": "Cassette",
    "vhs": "VHS",
    "other": "Other",
}
KINDS = {"movie": "Movie", "tv": "TV", "music": "Music", "other": "Other"}


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

def library_rows(con, viewer_id, query="", kind=""):
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
    sql += " ORDER BY t.name COLLATE NOCASE, i.format"
    return con.execute(sql, params).fetchall()


def visible_clause(query="", kind=""):
    where = ["(i.visibility = 'shared' OR i.owner_id = ?)"]
    params = []                              # viewer id is supplied by the caller
    if query:
        where.append("(t.name LIKE ? OR COALESCE(i.upc_ean, '') = ?)")
        params += ["%" + query + "%", query]
    if kind:
        where.append("t.kind = ?")
        params.append(kind)
    return " AND ".join(where), params


def library_stats(con, viewer_id, query="", kind=""):
    clause, extra = visible_clause(query, kind)
    params = [viewer_id] + extra
    base = ("FROM items i JOIN titles t ON t.id = i.title_id WHERE " + clause)
    titles = con.execute("SELECT COUNT(DISTINCT t.id) " + base, params).fetchone()[0]
    copies = con.execute("SELECT COUNT(*) " + base, params).fetchone()[0]
    formats = con.execute("SELECT i.format AS fmt, COUNT(*) AS n " + base +
                          " GROUP BY i.format ORDER BY n DESC", params).fetchall()
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
                 justify-content: center; font-size: .8rem; opacity: .8; }
  .card .thumb img { width: 100%; height: 100%; object-fit: cover; }
  .card .cap { padding: .4rem .5rem; font-size: .82rem; display: grid; gap: .15rem; }
  .card .cap .muted { font-size: .74rem; }
  label.check { display: flex; align-items: center; gap: .4rem; font-size: .85rem; }
  label.check input { width: auto; }
  .banner { padding: .5rem .7rem; border-radius: 6px; background: #c33;
            color: #fff; margin-bottom: 1rem; }
  .empty { opacity: .6; }
</style>
</head>
<body>
<h1>Media Collection</h1>
<p class="sub">Signed in as <strong>$viewer</strong> — you see everything shared
   plus your own private copies.</p>
<form class="search" method="get" action="$base">
  <input name="q" value="$q" placeholder="Search title or barcode">
  <select name="kind" onchange="this.form.submit()">$kind_options</select>
  <input type="hidden" name="view" value="$view">
  <button type="submit">Search</button>
</form>
<p class="sub">$viewtoggle$clear</p>
$notice
$error
<h2>$heading$scope</h2>
<p class="sub">$totals</p>
$library

<h2>Add a copy</h2>
<form class="add" method="post" action="${base}items">
  <input name="title" placeholder="Title" required>
  <input name="release_year" placeholder="Year" inputmode="numeric" pattern="[0-9]{0,4}">
  <select name="format">$format_options</select>
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
  <button type="submit">Add</button>
</form>

<h2>Owners</h2>
<table><tbody>$owners</tbody></table>

<p class="sub" style="margin-top:2rem"><a href="${base}admin">Library admin</a></p>
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
                 view="list", kind=""):
    rows = library_rows(con, viewer["id"], query, kind)
    titles, copies, formats = library_stats(con, viewer["id"], query, kind)
    heading = "%d title%s · %d cop%s" % (titles, "" if titles == 1 else "s", copies,
                                        "y" if copies == 1 else "ies")
    totals = " · ".join("%s %d" % (FORMAT_LABELS.get(row["fmt"], row["fmt"]), row["n"])
                        for row in formats) or "nothing yet"
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
        clause, extra = visible_clause(query, kind)
        cards = con.execute(
            "SELECT t.id, t.name, t.release_year, t.poster_url,"
            " COUNT(i.id) AS copies, group_concat(DISTINCT i.format) AS formats"
            " FROM titles t JOIN items i ON i.title_id = t.id"
            " WHERE " + clause +
            " GROUP BY t.id ORDER BY t.name COLLATE NOCASE",
            [viewer["id"]] + extra).fetchall()
        library = '<div class="cards">%s</div>' % ("".join(
            '<a class="card" href="{0}title/{1}"><div class="thumb">{2}</div>'
            '<div class="cap"><strong>{3}</strong><span class="muted">{4}</span></div></a>'.format(
                base, card["id"],
                '<img src="%s" alt="">' % html.escape(card["poster_url"])
                if card["poster_url"] else '<span class="muted">no image</span>',
                html.escape(card["name"]),
                html.escape(" · ".join(filter(None, [
                    str(card["release_year"] or ""),
                    " · ".join(FORMAT_LABELS.get(f, f)
                               for f in (card["formats"] or "").split(",")),
                    "%d %s" % (card["copies"],
                               "copy" if card["copies"] == 1 else "copies")])))
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
    owners = "".join(
        "<tr><td>{0}</td><td>{1} {2}</td></tr>".format(
            html.escape(o["owner"]), o["item_count"],
            "copy" if o["item_count"] == 1 else "copies")
        for o in owner_totals(con)
    ) or '<tr><td colspan="2" class="empty">No owners yet.</td></tr>'
    return PAGE.substitute(
        base=base,
        viewer=html.escape(viewer["display_name"]),
        error='<p class="banner">%s</p>' % html.escape(error) if error else "",
        notice=notice,
        count=len(rows),
        heading=heading,
        totals=totals,
        kind=kind,
        kind_options="".join(
            '<option value="%s"%s>%s</option>'
            % (value, " selected" if value == kind else "", label)
            for value, label in (("", "All media"), ("movie", "Films"), ("tv", "TV"),
                                 ("music", "Music"), ("other", "Other"))),
        scope=' matching “%s”' % html.escape(query) if query else "",
        q=html.escape(query),
        clear=' · <a href="%s">clear search</a>' % html.escape(base) if query else "",
        view=view,
        viewtoggle=' · <a href="%s?view=%s%s">%s</a>'
                   % (html.escape(base), "list" if view == "grid" else "grid",
                      "&amp;q=" + html.escape(query) if query else "",
                      "List view" if view == "grid" else "Thumbnails"),
        library=library,
        owners=owners,
        format_options="".join(
            '<option value="{0}">{1}</option>'.format(f, FORMAT_LABELS[f]) for f in FORMATS
        ),
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
            "INSERT INTO titles (name, release_year, created_by, imdb_id)"
            " VALUES (?, ?, ?, ?)", (title, year, viewer["id"], imdb)).lastrowid

    try:
        con.execute(
            "INSERT INTO items (title_id, owner_id, format, edition, upc_ean,"
            " location, visibility, added_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (title_id, viewer["id"], fmt, field(form, "edition") or None, upc,
             field(form, "location") or None, visibility, viewer["id"]),
        )
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
                return self.respond(200, ui_admin.render(
                    base, viewer, stats(con), merger.pending_count(con), message,
                    refresh_job.status(), len(dedupe.groups(con)),
                    con.execute("SELECT * FROM media_roots ORDER BY medium, label").fetchall()))

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
            merged = (parse_qs(urlparse(self.path).query).get("merged") or ["0"])[0]
            # detail page, always last so /title/<id>/metadata is handled in POST
            detail = re.search(r"/title/(\d+)/?$", route)
            if detail:
                return self.title_page(con, viewer, base, int(detail.group(1)))
            return self.respond(200, render_index(
                con, viewer, base, query=query,
                pending=merger.pending_count(con),
                merged=int(merged) if merged.isdigit() else 0,
                view="grid" if view == "grid" else "list",
                kind=kind if kind in ("movie", "tv", "music", "other") else ""))
        finally:
            con.close()

    def title_page(self, con, viewer, base, title_id, summary=None, error=None):
        row = con.execute("SELECT * FROM titles WHERE id = ?", (title_id,)).fetchone()
        if row is None:
            return self.respond(404, "No such title", "text/plain; charset=utf-8")
        items = con.execute(
            "SELECT * FROM items WHERE title_id = ? AND (visibility = 'shared' OR owner_id = ?)"
            " ORDER BY format, id", (title_id, viewer["id"])).fetchall()
        digital = con.execute(
            "SELECT d.*, i.format AS copy_format, r.label AS root_label"
            " FROM digital_sources d JOIN items i ON i.id = d.item_id"
            " LEFT JOIN media_roots r ON r.id = d.root_id"
            " WHERE i.title_id = ? ORDER BY d.provider", (title_id,)).fetchall()
        roots = con.execute("SELECT id, label, path FROM media_roots"
                            " ORDER BY medium, label").fetchall()
        return self.respond(200, ui_title.render(base, row, items, digital, FORMATS,
                                                 FORMAT_LABELS, summary=summary, error=error,
                                                 ripped_root=RIPPED_ROOT, roots=roots))

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
                medium = field(form, "medium") or "video"
                if not (label and path):
                    return self.respond(400, "Label and path are required.",
                                        "text/plain; charset=utf-8")
                try:
                    con.execute("INSERT INTO media_roots (label, path, medium)"
                                " VALUES (?, ?, ?)", (label, path, medium))
                    con.commit()
                except sqlite3.IntegrityError:
                    con.rollback()
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

            if route.rstrip("/").endswith("/merge"):
                result = merger.merge(con)
                added = result["items"] if result else 0
                return self.respond(303, "", extra={"Location": base + "admin?merged=%d" % added})

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
                    "SELECT d.id, i.title_id FROM digital_sources d"
                    " JOIN items i ON i.id = d.item_id WHERE d.id = ?",
                    (int(match.group(1)),)).fetchone()
                if source is None:
                    return self.respond(404, "No such source", "text/plain; charset=utf-8")
                con.execute("DELETE FROM digital_sources WHERE id = ?", (source["id"],))
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
                before = con.execute("SELECT imdb_id FROM titles WHERE id = ?",
                                     (title_id,)).fetchone()["imdb_id"]
                con.execute(
                    "UPDATE titles SET name = ?, release_year = ?, kind = ?, imdb_id = ?,"
                    " tmdb_id = ? WHERE id = ?",
                    (name, int(year) if len(year) == 4 else None, kind,
                     new_imdb, int(tmdb) if tmdb else None, title_id))
                con.commit()

                message = "Title updated."
                if new_imdb != before:
                    # the stored plot/poster belong to the old film - drop them and
                    # re-resolve straight from the id you just entered
                    con.execute("UPDATE titles SET overview = NULL, poster_url = NULL,"
                                " metadata_source = NULL WHERE id = ?", (title_id,))
                    con.commit()
                    if new_imdb:
                        message += " " + metadata.refresh(con, title_id)
                    else:
                        message += " Metadata cleared - refresh to look it up again."
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
                        "UPDATE items SET format = ?, edition = ?, upc_ean = ?, region = ?,"
                        " location = ?, condition = ?, visibility = ?, last_watched_on = ?,"
                        " updated_at = datetime('now') WHERE id = ?",
                        (fmt, field(form, "edition") or None, upc,
                         field(form, "region") or None, field(form, "location") or None,
                         field(form, "condition") or None,
                         "private" if field(form, "visibility") == "private" else "shared",
                         field(form, "last_watched_on") or None, item_id))
                except sqlite3.IntegrityError:
                    con.rollback()
                    return self.title_page(con, viewer, base, item["title_id"],
                                           error="That barcode is already on another copy.")
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
                return self.respond(303, "", extra={"Location": base})

            return self.respond(404, "Not found", "text/plain; charset=utf-8")
        finally:
            con.close()


def main():
    if not os.path.exists(DB_PATH):
        raise SystemExit("database missing at %s - run migrate.sh first" % DB_PATH)
    log("media collection listening on :%d (db=%s)" % (PORT, DB_PATH))
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
