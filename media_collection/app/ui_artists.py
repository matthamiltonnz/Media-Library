"""Artist list and artist page: who made the albums on the shelf."""

import html

import ui


def _text(value):
    if value is None:
        return ""
    return html.escape(str(value))


def _years(row):
    first, last = row["first_year"], row["last_year"]
    if first and last and first != last:
        return "%d–%d" % (first, last)
    if first or last:
        return str(first or last)
    return ""


def _cover(base, jf_id, poster_url):
    """Jellyfin's picture when the album is linked, else what MusicBrainz gave."""
    if jf_id:
        return base + "art/" + jf_id
    return poster_url or ""


def _thumb(base, jf_id, poster_url):
    cover = _cover(base, jf_id, poster_url)
    return '<div class="thumb case-jewel">%s</div>' % (
        '<img src="%s" alt="" loading="lazy">' % _text(cover) if cover else "")


def album_name(title_name, artist_name):
    """'Millennium' rather than 'Backstreet Boys — Millennium' on the artist page."""
    prefix = (artist_name or "") + " — "
    if title_name and title_name.startswith(prefix):
        return title_name[len(prefix):]
    return title_name or ""


def render_list(base, artists, view="grid", query=""):
    """Every act with albums held, as a table or as a wall of covers."""
    from urllib.parse import quote
    search = ('<form method="get" action="%sartists" class="sub">'
              '<input name="q" value="%s" placeholder="Search artists">'
              '<input type="hidden" name="view" value="%s"> '
              '<button type="submit">Search</button>%s</form>'
              % (_text(base), _text(query),
                 "grid" if view == "grid" else "list",
                 (' <a href="%sartists?view=%s">clear</a>'
                  % (_text(base), "grid" if view == "grid"
                     else "list")) if query else ""))
    if query and not artists:
        search += ('<p class="muted">No artist matches "%s".</p>'
                   % _text(query))
    toggle = ('<p class="sub"><a href="%sartists?view=%s%s">%s</a></p>'
              % (_text(base), "list" if view == "grid" else "grid",
                 ("&q=" + quote(query)) if query else "",
                 "List view" if view == "grid" else "Thumbnails"))
    head = ('<p class="sub"><a href="%s?kind=music&amp;view=grid">Back to the albums</a> · '
            '%d artist%s</p>' % (_text(base), len(artists),
                                 "" if len(artists) == 1 else "s"))
    if not artists:
        note = ('' if query else
                '<p class="muted">No artists yet - albums pick one up'
                ' from their artist field.</p>')
        return ui.page(base, "Artists", search + note,
                       back="?kind=music&view=grid")
    if view == "grid":
        cards = "".join(
            '<a class="card" href="%sartist/%d">%s<div class="cap">'
            '<strong>%s</strong><span class="muted">%d album%s%s</span>'
            '</div></a>'
            % (_text(base), row["id"],
               _thumb(base, row["jf_id"], row["poster_url"]),
               _text(row["name"]), row["albums"],
               "" if row["albums"] == 1 else "s",
               " · " + _years(row) if _years(row) else "")
            for row in artists)
        body = head + toggle + '<div class="cards">%s</div>' % cards
    else:
        rows = "".join(
            '<tr><td><a href="%sartist/%d">%s</a></td>'
            '<td>%d</td><td class="muted">%s</td></tr>'
            % (_text(base), row["id"], _text(row["name"]), row["albums"],
               _years(row))
            for row in artists)
        body = head + toggle + ('<table class="artists"><tr><th>Artist</th>'
                                '<th>Albums</th><th>Years</th></tr>%s</table>'
                                % rows)
    return ui.page(base, "Artists", search + body,
                   back="?kind=music&view=grid")


def render_artist(base, artist, titles, labels, view="grid"):
    """One act: what is known about them and the albums that belong to them."""
    info = []
    if artist["musicbrainz_id"]:
        info.append('<a href="https://musicbrainz.org/artist/%s" target="_blank"'
                    ' rel="noopener">MusicBrainz</a>'
                    % _text(artist["musicbrainz_id"]))
    else:
        info.append('<a href="https://musicbrainz.org/search?query=%s&amp;type='
                    'artist" target="_blank" rel="noopener">Look up on'
                    ' MusicBrainz</a>'
                    % _text(html.escape(artist["name"]).replace(" ", "+")))
    if artist["country"]:
        info.append(_text(artist["country"]))
    if artist["formed_year"]:
        info.append("formed %d" % artist["formed_year"])

    toggle = ('<p class="sub"><a href="%sartist/%d?view=%s">%s</a></p>'
              % (_text(base), artist["id"],
                 "list" if view == "grid" else "grid",
                 "List view" if view == "grid" else "Thumbnails"))
    head = ('<p class="sub"><a href="%sartists">All artists</a> · %s</p>%s'
            % (_text(base), " · ".join(info),
               '<p>%s</p>' % _text(artist["overview"])
               if artist["overview"] else ""))
    count = ('<p class="sub">%d album%s</p>'
             % (len(titles), "" if len(titles) == 1 else "s"))

    if view == "grid":
        cards = "".join(
            '<a class="card" href="%stitle/%d">%s<div class="cap">'
            '<strong>%s</strong><span class="muted">%s</span></div></a>'
            % (_text(base), row["id"],
               _thumb(base, row["jf_id"], row["poster_url"]),
               _text(album_name(row["name"], artist["name"])),
               _text(" · ".join(filter(None, [str(row["release_year"] or ""),
                                              _formats(row, labels)]))))
            for row in titles)
        body = head + count + toggle + '<div class="cards">%s</div>' % cards
    else:
        rows = "".join(
            '<tr><td><a href="%stitle/%d">%s</a></td><td>%s</td>'
            '<td class="muted">%s</td><td class="muted">%s</td></tr>'
            % (_text(base), row["id"],
               _text(album_name(row["name"], artist["name"])),
               _text(row["release_year"] or ""), _text(_formats(row, labels)),
               _text(row["last_watched_on"] or "not watched"))
            for row in titles)
        body = (head + count + toggle + '<table><tr><th>Album</th><th>Year</th>'
                '<th>Owned as</th><th>Watched</th></tr>%s</table>' % rows)
    return ui.page(base, artist["name"], body, back="artists")


def _formats(row, labels):
    return ", ".join(labels.get(fmt, fmt)
                     for fmt in (row["formats"] or "").split(",") if fmt)
