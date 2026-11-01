"""CSV import: parse a Blu-ray.com style export, map columns, stage for review.

Staging never touches the catalogue; commit() only inserts rows whose status is
'ok'. Duplicates are detected against existing items and within the file itself.
"""
import csv
import io
import json
import re

FIELDS = [
    ("title", "Title"),
    ("year", "Year"),
    ("format", "Format"),
    ("type", "Type (movie/TV)"),
    ("upc", "UPC"),
    ("ean", "EAN (fallback barcode)"),
    ("region", "Region"),
    ("bluray_com_id", "Blu-ray.com ID"),
    ("bluray_discs", "Blu-ray disc count"),
    ("dvd_discs", "DVD disc count"),
    ("edition", "Edition"),
    ("location", "Storage location"),
    ("condition", "Condition"),
    ("imdb_id", "IMDb ID"),
    ("provider", "Digital provider"),
]

ALIASES = {
    "title": ["title", "name", "movie", "film"],
    "year": ["year", "release year", "released", "release_date"],
    "format": ["format", "media", "media type"],
    "type": ["type", "kind", "category"],
    "upc": ["upc", "barcode", "upc/ean", "upc / ean"],
    "ean": ["ean"],
    "bluray_discs": ["blu-ray discs", "bluray discs", "blu-ray disc", "bluray disc"],
    "dvd_discs": ["dvd discs", "dvd disc"],
    "region": ["region", "region code", "regions", "region(s)"],
    "bluray_com_id": ["blu-ray.com id", "bluray.com id", "blu-ray.com movie id",
                      "bluray_id", "blu-ray id", "bluraycomid"],
    "edition": ["edition", "version"],
    "location": ["location", "storage", "shelf", "where"],
    "condition": ["condition", "state"],
    "imdb_id": ["imdb", "imdb id", "imdbid"],
    "provider": ["digital", "digital copy", "digital source", "provider", "source"],
}

FORMAT_ALIASES = {
    "blu-ray 3d": "bluray_3d", "bluray 3d": "bluray_3d", "blu-ray3d": "bluray_3d",
    "3d": "bluray_3d",
    "blu-ray": "bluray", "bluray": "bluray", "bd": "bluray",
    "4k ultra hd": "uhd_4k", "4k": "uhd_4k", "uhd": "uhd_4k", "4k uhd": "uhd_4k",
    "dvd": "dvd", "digital": "digital", "digital hd": "digital", "itunes": "digital",
    "vhs": "vhs",
    "cd": "cd", "audio cd": "cd", "compact disc": "cd",
    "vinyl": "vinyl", "lp": "vinyl", "12\" vinyl": "vinyl",
    "sacd": "sacd", "cassette": "cassette", "tape": "cassette",
}

PROVIDERS = {"itunes": "itunes", "apple": "itunes", "apple tv": "appletv",
             "appletv": "appletv", "jellyfin": "jellyfin"}


def clean(value):
    return re.sub(r"\s+", " ", (value or "")).strip().strip('"')


def digits(value):
    return re.sub(r"\D", "", clean(value))


def normalize_format(value):
    return FORMAT_ALIASES.get(clean(value).lower(), "other")


def normalize_kind(value):
    key = clean(value).lower()
    return "tv" if ("tv" in key or "series" in key or "show" in key) else "movie"


def normalize_provider(value):
    key = clean(value).lower()
    for needle, provider in PROVIDERS.items():
        if needle in key:
            return provider
    if key in ("1", "y", "yes", "true"):   # a Blu-ray.com "Digital copy" flag
        return "itunes"                    # the household's store is Apple TV
    return None


def parse_csv(text):
    """Return (headers, records). Tolerates , ; or tab delimited and blank lines."""
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(clean(c) for c in r)]
    if not rows:
        return [], []
    headers = [clean(h) for h in rows[0]]
    # Blu-ray.com exports omit the ASIN column from the header line, so every data
    # row is one field wider than the header. Re-insert it after UPC/EAN before zipping.
    width = len(rows[0])
    if any(len(r) == width + 1 for r in rows[1:]) and all(
            len(r) == width + 1 for r in rows[1:]):
        barcode_cols = [i for i, h in enumerate(headers) if h.lower() in ("ean", "upc")]
        if barcode_cols:
            headers.insert(max(barcode_cols) + 1, "ASIN")
    records = []
    for raw in rows[1:]:
        records.append({h: (raw[i] if i < len(raw) else "") for i, h in enumerate(headers)})
    return headers, records


def guess_mapping(headers):
    lowered = {h.lower(): h for h in headers}
    mapping = {}
    for field, labels in ALIASES.items():
        for label in labels:
            if label in lowered:
                mapping[field] = lowered[label]
                break
        if field in mapping:
            continue
        for header in headers:
            if any(label in header.lower() for label in labels):
                mapping[field] = header
                break
    return mapping



def stage(con, import_row, mapping):
    """Replace staged rows for this import using the given {field: header} map."""
    _, records = parse_csv(import_row["raw_csv"])
    con.execute("DELETE FROM import_rows WHERE import_id = ?", (import_row["id"],))

    seen_upc = {}
    for line_no, record in enumerate(records, start=2):
        def value(field):
            return clean(record.get(mapping.get(field, ""), ""))

        title = value("title")
        upc = digits(value("upc")) or digits(value("ean")) or None
        year_text = value("year")
        year_raw = digits(year_text)
        year = int(year_raw) if len(year_raw) == 4 else None
        bluray_com_raw = digits(value("bluray_com_id"))
        status, message = "ok", None

        if not title:
            status, message = "error", "no title in this row"
        elif year_text and year is None:
            status, message = "error", "year is not 4 digits"
        elif upc and len(upc) not in (12, 13):
            status, message = "error", "barcode is not 12 or 13 digits"
        elif upc and upc in seen_upc:
            status, message = "duplicate", "same barcode on line %d" % seen_upc[upc]
        elif upc:
            owner = con.execute(
                "SELECT u.display_name FROM items i JOIN users u ON u.id = i.owner_id "
                "WHERE i.upc_ean = ?", (upc,)
            ).fetchone()
            if owner:      # someone already has this disc: this row is a second copy
                message = "also held by %s" % owner[0]

        if upc and status == "ok":
            seen_upc[upc] = line_no

        fmt = normalize_format(value("format"))
        if fmt == "other":
            bluray_count = digits(value("bluray_discs"))
            dvd_count = digits(value("dvd_discs"))
            if bluray_count and bluray_count != "0":
                fmt = "bluray"
            elif dvd_count and dvd_count != "0":
                fmt = "dvd"

        con.execute(
            "INSERT INTO import_rows (import_id, line_no, title, release_year, kind,"
            " upc_ean, format, region, bluray_com_id, edition, location, condition,"
            " provider, status, message, raw_json)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                import_row["id"], line_no, title or None, year,
                normalize_kind(value("type")) if value("type") else "movie",
                upc, fmt, value("region") or None,
                int(bluray_com_raw) if bluray_com_raw else None,
                value("edition") or None, value("location") or None,
                value("condition") or None, normalize_provider(value("provider")),
                status, message, json.dumps(record, ensure_ascii=False),
            ),
        )
    con.execute("UPDATE imports SET mapping_json = ?, row_count = ? WHERE id = ?",
                (json.dumps(mapping), len(records), import_row["id"]))
    con.commit()
    return len(records)


def commit(con, import_row, viewer_id, default_visibility="shared"):
    """Insert staged 'ok' rows. Returns (imported, failed) or None if already done."""
    if import_row["committed_at"]:
        return None
    rows = con.execute(
        "SELECT * FROM import_rows WHERE import_id = ? AND status = 'ok' ORDER BY line_no",
        (import_row["id"],),
    ).fetchall()
    imported = failed = 0
    for row in rows:
        try:
            title = con.execute(
                "SELECT id FROM titles WHERE lower(name) = lower(?) "
                "AND COALESCE(release_year, -1) = COALESCE(?, -1)",
                (row["title"], row["release_year"]),
            ).fetchone()
            if title:
                title_id = title["id"]
            else:
                title_id = con.execute(
                    "INSERT INTO titles (name, release_year, kind, created_by)"
                    " VALUES (?, ?, ?, ?)",
                    (row["title"], row["release_year"], row["kind"] or "movie", viewer_id),
                ).lastrowid
            item_id = con.execute(
                "INSERT INTO items (title_id, owner_id, format, edition, upc_ean, region,"
                " bluray_com_id, location, condition, visibility, added_by)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (title_id, viewer_id, row["format"] or "other", row["edition"],
                 row["upc_ean"], row["region"], row["bluray_com_id"], row["location"],
                 row["condition"], default_visibility, viewer_id),
            ).lastrowid
            if row["provider"]:
                con.execute(
                    "INSERT OR IGNORE INTO digital_sources (item_id, provider)"
                    " VALUES (?, ?)", (item_id, row["provider"]),
                )
            imported += 1
        except Exception as exc:                        # keep the rest of the batch
            failed += 1
            con.execute("UPDATE import_rows SET status = 'error', message = ? WHERE id = ?",
                        ("not imported: %s" % exc, row["id"]))
    con.execute("UPDATE imports SET committed_at = datetime('now') WHERE id = ?",
                (import_row["id"],))
    con.commit()
    return imported, failed
