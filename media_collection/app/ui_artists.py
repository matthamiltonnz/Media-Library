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


def render_list(base, artists):
    """Every act with albums held, as a plain A–Z table."""
    if not artists:
        return ui.page(base, "Artists", '<p class="muted">No artists yet - music'
                       ' titles pick one up from their "Artist — Album" name.</p>',
                       back="?kind=music&view=grid")
    rows = "".join(
        '<tr><td><a href="%sartist/%d">%s</a></td>'
        '<td>%d</td><td class="muted">%s</td></tr>'
        % (_text(base), row["id"], _text(row["name"]), row["albums"],
           _years(row))
        for row in artists)
    body = """
<p class="sub"><a href="%smusic">Back to the albums</a></p>
<p class="sub">%d artist%s</p>
<table class="artists">
  <tr><th>Artist</th><th>Albums</th><th>Years</th></tr>%s
</table>""" % (_text(base), len(artists), "" if len(artists) == 1 else "s", rows)
    return ui.page(base, "Artists", body, back="?kind=music&view=grid")


def render_artist(base, artist, titles, labels):
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

    rows = "".join(
        '<tr><td><a href="%stitle/%d">%s</a></td><td>%s</td>'
        '<td class="muted">%s</td><td class="muted">%s</td></tr>'
        % (_text(base), row["id"],
           _text(album_name(row["name"], artist["name"])),
           _text(row["release_year"] or ""),
           _text(", ".join(labels.get(fmt, fmt)
                           for fmt in (row["formats"] or "").split(",") if fmt)),
           _text(row["last_watched_on"] or "not watched"))
        for row in titles)
    body = """
<p class="sub"><a href="%sartists">All artists</a></p>
<p class="sub">%s</p>
%s
<p class="sub">%d album%s</p>
<table>
  <tr><th>Album</th><th>Year</th><th>Owned as</th><th>Watched</th></tr>%s
</table>""" % (_text(base), " · ".join(info),
               '<p>%s</p>' % _text(artist["overview"])
               if artist["overview"] else "",
               len(titles), "" if len(titles) == 1 else "s", rows)
    return ui.page(base, artist["name"], body, back="artists")


def album_name(title_name, artist_name):
    """'Millennium' rather than 'Backstreet Boys — Millennium' on the artist page."""
    prefix = (artist_name or "") + " — "
    if title_name and title_name.startswith(prefix):
        return title_name[len(prefix):]
    return title_name or ""
