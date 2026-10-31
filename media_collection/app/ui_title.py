"""Title detail page: metadata, editable copies, last watched."""
import html

import ui


def _text(value):
    return html.escape(value or "")


def _facts(row):
    bits = []
    if row["release_year"]:
        bits.append(str(row["release_year"]))
    if row["kind"] and row["kind"] != "movie":
        bits.append(row["kind"].upper())
    if row["runtime_minutes"]:
        bits.append("%d min" % row["runtime_minutes"])
    if row["genres"]:
        bits.append(row["genres"])
    if row["rating"]:
        bits.append(row["rating"])
    return " · ".join(bits)


def _links(row):
    links = []
    if row["imdb_id"]:
        links.append('<a href="https://www.imdb.com/title/%s/" target="_blank" rel="noopener">'
                     "IMDb %s</a>" % (_text(row["imdb_id"]), _text(row["imdb_id"])))
    else:
        links.append('<a href="https://www.imdb.com/find/?q=%s" target="_blank" '
                     'rel="noopener">Search IMDb for “%s”</a>'
                     % (html.escape(html.escape(row["name"]), quote=True),
                        _text(row["name"])))
    if row["tmdb_id"]:
        links.append('<a href="https://www.themoviedb.org/movie/%s" target="_blank" '
                     'rel="noopener">TMDB %s</a>' % (row["tmdb_id"], row["tmdb_id"]))
    if "musicbrainz_id" in row.keys() and row["musicbrainz_id"]:
        links.append('<a href="https://musicbrainz.org/release-group/%s" '
                     'target="_blank" rel="noopener">MusicBrainz</a>'
                     % _text(row["musicbrainz_id"]))
    if row["metadata_updated_at"]:
        links.append('<span class="muted">%s via %s</span>'
                     % (_text(row["metadata_updated_at"]),
                        _text(row["metadata_source"] or "manual")))
    return " · ".join(links)


def _item_row(base, item, formats, labels):
    options = "".join(
        '<option value="%s"%s>%s</option>'
        % (fmt, " selected" if fmt == item["format"] else "", labels[fmt])
        for fmt in formats
    )
    watched = item["last_watched_on"] or ""
    bits = ['<span class="tag">%s</span>' % labels[item["format"]]]
    bits.append(item["upc_ean"] or "no barcode")
    bits.append("shared" if item["visibility"] == "shared" else "private")
    if item["location"]:
        bits.append(_text(item["location"]))
    bits.append("watched " + watched if watched else "not watched")
    return f"""
<details class="copy">
<summary>{" · ".join(bits)} <span class="muted">— edit</span></summary>
<form method="post" action="{base}items/{item['id']}">
  <div class="grid">
    <label>Format<select name="format">{options}</select></label>
    <label>Edition<input name="edition" value="{_text(item['edition'])}"></label>
    <label>UPC / EAN<input name="upc_ean" value="{_text(item['upc_ean'])}"></label>
    <label>Region<input name="region" value="{_text(item['region'])}"></label>
    <label>Location<input name="location" value="{_text(item['location'])}"></label>
    <label>Condition<input name="condition" value="{_text(item['condition'])}"></label>
    <label>Visibility<select name="visibility">
      <option value="shared"{" selected" if item['visibility'] == 'shared' else ""}>Shared</option>
      <option value="private"{" selected" if item['visibility'] == 'private' else ""}>Private</option>
    </select></label>
    <label>Last watched<input type="date" name="last_watched_on" value="{watched}"></label>
  </div>
  <p>
    <button type="submit">Save this copy</button>
    <button type="submit" formaction="{base}items/{item['id']}/watched">Watched today</button>
    <button type="submit" formaction="{base}items/{item['id']}/delete">Delete copy</button>
  </p>
</form>
</details>
"""


def render(base, row, items, digital, formats, labels, summary=None, error=None,
           ripped_root="", roots=()):
    parts = []
    if summary:
        parts.append('<p class="banner good">%s</p>' % _text(summary))
    if error:
        parts.append('<p class="banner bad">%s</p>' % _text(error))

    if row["poster_url"]:
        parts.append('<img class="poster" src="%s" alt="">' % _text(row["poster_url"]))
    parts.append("<p>%s</p>" % (_facts(row) or '<span class="muted">year unknown</span>'))
    parts.append("<p>%s</p>" % _links(row))
    if row["overview"]:
        parts.append("<p>%s</p>" % _text(row["overview"]))

    parts.append(
        '<form method="post" action="%stitle/%d/metadata">'
        "<button type=\"submit\">Refresh metadata</button>"
        ' <span class="muted">Wikipedia + Wikidata — no API key needed</span></form>'
        % (base, row["id"]))

    parts.append("<h2>Edit title</h2>")
    kind_options = "".join(
        '<option value="%s"%s>%s</option>'
        % (kind, " selected" if kind == row["kind"] else "", label)
        for kind, label in (("movie", "Movie"), ("tv", "TV"), ("music", "Music"),
                            ("other", "Other")))
    parts.append(f"""
<form method="post" action="{base}title/{row['id']}">
  <div class="grid">
    <label>Name<input name="name" value="{_text(row['name'])}" required></label>
    <label>Year<input name="release_year" value="{row['release_year'] or ''}" inputmode="numeric"></label>
    <label>Kind<select name="kind">{kind_options}</select></label>
    <label>IMDb ID<input name="imdb_id" value="{_text(row['imdb_id'])}" placeholder="tt..."></label>
    <label>TMDB ID<input name="tmdb_id" value="{row['tmdb_id'] or ''}" inputmode="numeric"></label>
  </div>
  <p><button type="submit">Save title</button>
     <span class="muted">Changing the IMDb ID re-fetches the plot, poster and year from that film.</span></p>
</form>""")

    parts.append("<h2>Copies owned (%d)</h2>" % len(items))
    parts.append('<p class="sub">Each copy is stored separately — format, barcode, location, '
                 "who owns it and when it was last watched. <strong>Save this copy</strong> "
                 "changes only the copy you opened; the title itself is edited above.</p>")
    if items:
        parts.append("".join(_item_row(base, item, formats, labels) for item in items))
    else:
        parts.append('<p class="muted">No copies recorded.</p>')

    parts.append("<h2>Playback sources</h2>")
    parts.append('<p class="sub">Where this title can be played from — a ripped file on '
                 'the NAS, a Jellyfin/Kodi item, an iTunes purchase. Ripped files are '
                 'stored relative to <code>%s</code>.</p>' % _text(ripped_root))
    if digital:
        parts.append("<table><thead><tr><th>Copy</th><th>Provider</th><th>Root</th>"
                     "<th>Path / id</th><th></th></tr></thead><tbody>")
        for source in digital:
            detail = source["path"] or " ".join(
                filter(None, [source["library_id"], source["external_id"]])) or "—"
            parts.append(
                "<tr><td>%s</td><td>%s</td><td>%s</td><td class=\"barcode\">%s</td>"
                "<td><form method=\"post\" action=\"%ssources/%d/delete\">"
                "<button type=\"submit\">Remove</button></form></td></tr>"
                % (_text(labels.get(source["copy_format"], source["copy_format"])),
                   _text(source["provider"]), _text(source["root_label"] or "—"),
                   _text(detail), base, source["id"]))
        parts.append("</tbody></table>")
    else:
        parts.append('<p class="muted">No playback source recorded yet.</p>')

    if items and roots:
        copy_options = "".join(
            '<option value="%d">%s%s</option>'
            % (item["id"], labels.get(item["format"], item["format"]),
               " · " + item["upc_ean"] if item["upc_ean"] else "")
            for item in items)
        provider_options = "".join(
            '<option value="%s"%s>%s</option>'
            % (provider, " selected" if provider == "rip" else "", label)
            for provider, label in (("rip", "Ripped file (NAS)"), ("jellyfin", "Jellyfin"),
                                    ("kodi", "Kodi"), ("itunes", "iTunes"),
                                    ("appletv", "Apple TV"), ("other", "Other")))
        root_options = "".join(
            '<option value="%d">%s — %s</option>'
            % (root["id"], _text(root["label"]), _text(root["path"]))
            for root in roots)
        parts.append(f"""
<form method="post" action="{base}title/{row['id']}/sources">
  <div class="grid">
    <label>Copy<select name="item_id">{copy_options}</select></label>
    <label>Provider<select name="provider">{provider_options}</select></label>
    <label>NAS root<select name="root_id">{root_options}</select></label>
    <label>Path inside the root<input name="path" placeholder="Movie (Year)/Movie.mkv"></label>
    <label>Library id<input name="library_id" placeholder="Jellyfin library / Kodi source"></label>
    <label>Item id<input name="external_id" placeholder="Jellyfin item / Kodi db id"></label>
    <label>Notes<input name="notes"></label>
  </div>
  <p><button type="submit">Add playback source</button>
     <span class="muted">The full path is the root plus what you type here.</span></p>
</form>""")

    kind = row["kind"] if "kind" in row.keys() else "movie"
    return ui.page(base, row["name"], "".join(parts), back="?kind=%s" % kind)
