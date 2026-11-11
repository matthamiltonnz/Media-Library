"""Artist list and artist page: who made the albums on the shelf."""

import html
import json
import urllib.parse

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
    return ui.cover_url(base, poster_url)


def _thumb(base, jf_id, poster_url, portrait=""):
    """The act's own photograph when there is one, else its first album's cover.

    An artist card used to show the sleeve of whichever album came first, which
    is how a wall of acts read as a wall of records.
    """
    if (portrait or "").strip():
        return ('<div class="thumb face"><img src="%s" alt="" loading="lazy">'
                '</div>' % _text(ui.cover_url(base, portrait)))
    cover = _cover(base, jf_id, poster_url)
    return '<div class="thumb case-jewel">%s</div>' % (
        '<img src="%s" alt="" loading="lazy">' % _text(cover) if cover else "")


def album_name(title_name, artist_name):
    """'Millennium' rather than 'Backstreet Boys — Millennium' on the artist page."""
    prefix = (artist_name or "") + " — "
    if title_name and title_name.startswith(prefix):
        return title_name[len(prefix):]
    return title_name or ""


def render_list(base, artists, view="grid", query="", owners=None,
                chosen=None, total=None):
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
    if chosen is not None:
        # a search from a filtered list should stay inside that owner
        search = search.replace(
            "</form>",
            '<input type="hidden" name="owner" value="%d"></form>' % chosen, 1)
    if owners:
        keep = "&amp;q=" + quote(query) if query else ""
        parts = ['<a class="%s" href="%sartists?view=%s%s">Everyone%s</a>'
                 % ("current" if chosen is None else "", _text(base), view,
                    keep,
                    '<span class="n">%d</span>' % total if total else "")]
        parts += ['<a class="%s" href="%sartists?view=%s&amp;owner=%d%s">%s'
                  '<span class="n">%d</span></a>'
                  % ("current" if chosen == row["id"] else "", _text(base),
                     view, row["id"], keep, _text(row["name"]), row["count"])
                  for row in owners]
        search += '<nav class="modes owners">%s</nav>' % "".join(parts)
    toggle = ('<p class="sub"><a href="%sartists?view=%s%s">%s</a></p>'
              % (_text(base), "list" if view == "grid" else "grid",
                 (("&q=" + quote(query)) if query else "")
                 + (("&amp;owner=%d" % chosen)
                    if chosen is not None else ""),
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
               _thumb(base, row["jf_id"], row["poster_url"],
                      row["image_url"]),
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


def render_artist(base, artist, titles, labels, view="grid",
                  notice="", bad=False):
    """One act: the biography, where to read more, and the albums."""
    # The links are read first because the line under the title must not repeat
    # one of them: MusicBrainz appeared twice on this page - once beside the
    # country, once in "Read more" - whenever the act had a biography.
    found = []
    try:
        found = [item for item in json.loads(artist["link_urls"] or "[]")
                 if item.get("url")]
    except (ValueError, AttributeError):
        found = []
    sources = " \u00b7 ".join('<a href="%s" target="_blank" rel="noopener">%s</a>'
                             % (_text(item["url"]), _text(item.get("label") or "link"))
                             for item in found)
    linked = set(item.get("label") or "" for item in found)

    info = []
    if not artist["musicbrainz_id"]:
        # no id yet: offer the search, which is a different link altogether
        info.append('<a href="https://musicbrainz.org/search?query=%s&amp;type='
                    'artist" target="_blank" rel="noopener">Look up on'
                    ' MusicBrainz</a>'
                    % _text(html.escape(artist["name"]).replace(" ", "+")))
    elif "MusicBrainz" not in linked:
        # only when it is not already one of the "Read more" links below
        info.append('<a href="https://musicbrainz.org/artist/%s" target="_blank"'
                    ' rel="noopener">MusicBrainz</a>'
                    % _text(artist["musicbrainz_id"]))
    if artist["country"]:
        info.append(_text(artist["country"]))
    if artist["formed_year"]:
        # a band is formed, a person is born
        word = "born" if (("kind" in artist.keys())
                          and artist["kind"] == "person") else "formed"
        info.append("%s %d" % (word, artist["formed_year"]))

    toggle = ('<p class="sub"><a href="%sartist/%d?view=%s">%s</a></p>'
              % (_text(base), artist["id"],
                 "list" if view == "grid" else "grid",
                 "List view" if view == "grid" else "Thumbnails"))
    head = ('<p class="sub"><a href="%sartists">All artists</a> \u00b7 %s</p>'
            % (_text(base), " \u00b7 ".join(info)))
    count = ('<p class="sub">%d album%s</p>'
             % (len(titles), "" if len(titles) == 1 else "s"))

    # The biography sits above the albums: a portrait, what Wikipedia says,
    # and where to read more. MusicBrainz's one-word "overview" ("Group") is
    # not shown at all - only an act with a real article carries a wiki_url.
    picture = (artist["image_url"] or "").strip()
    if picture:
        # the photograph, with the pencil that changes it on its corner
        portrait = ('<span class="pfw"><img class="portrait" src="%s" alt=""'
                    ' loading="lazy"><a class="coveredit" href="%sartist/%d/art"'
                    ' title="Choose a picture">&#9998;</a></span>'
                    % (_text(ui.cover_url(base, picture)), _text(base),
                       artist["id"]))
    else:
        portrait = ""
    if (artist["overview"] or "").strip() and artist["wiki_url"]:
        bio = ('<div class="bio">%s<div><p>%s</p>%s</div></div>'
               % (portrait, _text(artist["overview"]),
                  '<p class="sub">Read more: %s</p>' % sources
                  if sources else ""))
    elif portrait:
        bio = ('<div class="bio">%s<div><p class="muted">No biography yet - '
               'press <em>Fetch details</em> and it is looked up on '
               'Wikipedia.</p></div></div>' % portrait)
    else:
        bio = ('<p class="muted">No biography yet - press <em>Fetch details'
               '</em> and it is looked up on Wikipedia.</p>')
    details = ('<div class="acts">'
               '<form method="post" action="%sartist/%d/bio">'
               '<button type="submit">%s</button></form>'
               '<a href="%sartist/%d/art">%s</a></div>'
               % (_text(base), artist["id"],
                  "Fetch details again" if artist["wiki_url"] else "Fetch details",
                  _text(base), artist["id"],
                  "Change the picture" if picture else "Choose a picture"))

    rename = ('<form method="post" action="%sartist/%d" class="sub">'
              '<label>Name<input name="artist_name" value="%s"></label> '
              '<button type="submit">Rename artist</button></form>'
              % (_text(base), artist["id"], _text(artist["name"])))
    # A wrong or missing id is why an act ends up with a MusicBrainz link to
    # nothing (or to the wrong act), so it can be typed in or corrected here.
    # `action=mbid` tells the POST handler which of the two forms this was, and
    # is always sent - a blank box means "clear it".
    mbid = ('<form method="post" action="%sartist/%d" class="sub">'
            '<input type="hidden" name="action" value="mbid">'
            '<label>MusicBrainz id<input name="musicbrainz_id" value="%s"'
            ' size="38" placeholder="00000000-0000-0000-0000-000000000000">'
            '</label> <button type="submit">Save id</button>'
            '<span class="muted"> the 36-character uuid in the artist\'s own'
            ' MusicBrainz URL</span></form>'
            ' <a href="%sartist/%d/mbid">Wrong act? Find the right entry</a>'
            % (_text(base), artist["id"],
               _text((artist["musicbrainz_id"] if "musicbrainz_id"
                       in artist.keys() else "") or ""),
               _text(base), artist["id"]))

    # `from` carries this page, so a title opened from here comes back here
    here = _text(urllib.parse.quote("artist/%d?view=%s"
                                    % (artist["id"], view), safe=""))
    if view == "grid":
        cards = "".join(
            '<a class="card" href="%stitle/%d?from=%s">%s<div class="cap">'
            '<strong>%s</strong><span class="muted">%s</span></div></a>'
            % (_text(base), row["id"], here,
               _thumb(base, row["jf_id"], row["poster_url"]),
               _text(album_name(row["name"], artist["name"])),
               _text(" \u00b7 ".join(filter(None, [str(row["release_year"] or ""),
                                                  _formats(row, labels)]))))
            for row in titles)
        body = (bio + details + count + toggle
                + '<div class="cards">%s</div>' % cards)
    else:
        rows = "".join(
            '<tr><td><a href="%stitle/%d?from=%s">%s</a></td><td>%s</td>'
            '<td class="muted">%s</td><td class="muted">%s</td></tr>'
            % (_text(base), row["id"], here,
               _text(album_name(row["name"], artist["name"])),
               _text(row["release_year"] or ""), _text(_formats(row, labels)),
               _text(row["last_watched_on"] or "not watched"))
            for row in titles)
        body = (bio + details + count + toggle
                + '<table><tr><th>Album</th><th>Year</th><th>Owned as</th>'
                  '<th>Watched</th></tr>%s</table>' % rows)
    banner = ('<p class="banner %s">%s</p>'
              % ("bad" if bad else "good", _text(notice))
              if notice else "")
    return ui.page(base, artist["name"],
                    banner + head + rename + mbid + body, back="artists")


def _formats(row, labels):
    """The formats on the shelf, with a play mark instead of "Ripped
    file": a ripped copy is a Jellyfin route, and saying so in words
    on every album card was noise."""
    raw = [fmt for fmt in (row["formats"] or "").split(",") if fmt]
    shown = [labels.get(fmt, fmt) for fmt in raw if fmt != "ripped"]
    text = ", ".join(shown)
    if "ripped" in raw:
        mark = '▶'   # escaped by _text(), so a glyph, not a span
        text = text + " " + mark if text else mark
    return text


def render_art(base, artist, choices, note="", bad=False):
    """Pictures of one act: what the lookup found, and an upload of your own."""
    name = _text(artist["name"])
    current = (artist["image_url"] or "").strip()
    here = _text(base) + "artist/%d" % artist["id"]
    tiles = "".join(
        '<form method="post" action="%s/art">'
        '<input type="hidden" name="action" value="use">'
        '<input type="hidden" name="url" value="%s">'
        '<button type="submit" title="Use this picture">'
        '<img src="%s" alt="" loading="lazy">'
        '<span>%s</span></button></form>'
        % (here, _text(item["url"]),
           _text(ui.cover_url(base, item["url"])),
           _text(item.get("label") or item.get("source") or ""))
        for item in choices)
    notice = ('<p class="banner %s">%s</p>'
              % ("bad" if bad else "good", _text(note))) if note else ""
    shown = ""
    if current:
        shown = ('<p class="sub">The picture on %s\'s page now:</p>'
                 '<p><img class="portrait" src="%s" alt=""></p>'
                 '<form method="post" action="%s/art">'
                 '<input type="hidden" name="action" value="forget">'
                 '<input type="hidden" name="url" value="%s">'
                 '<button type="submit">Remove this picture</button>'
                 '</form>'
                 % (name, _text(ui.cover_url(base, current)), here,
                    _text(current)))
    body = ('%s%s<p class="sub">Pick one of these, or upload your own.</p>'
            '<div class="covers">%s</div>'
            '<form method="post" action="%s/art" class="sub">'
            '<input type="hidden" name="action" value="search">'
            '<button type="submit">Search again</button>'
            '<span class="muted"> Wikipedia, Wikidata and the act\'s own '
            'MusicBrainz links.</span></form>'
            '<form class="upload" method="post" action="%s/cover"'
            ' enctype="multipart/form-data">'
            '<h3>Upload a picture</h3>'
            '<label>Image file<input type="file" name="image"'
            ' accept="image/png,image/jpeg,image/gif,image/webp"></label>'
            ' <button type="submit">Upload</button>'
            '<p class="sub">Your own photograph beats anything a lookup finds'
            ' - up to 6 MB, PNG, JPEG, GIF or WebP.</p></form>'
            % (notice, shown, tiles, here, here))
    return ui.page(base, "Pictures of " + artist["name"], body,
                   back="artist/%d" % artist["id"],
                   back_label="\u2190 Back to the artist")


def render_mbid(base, artist, matches, note="", query=""):
    """Pick the right MusicBrainz entry for an act whose id is wrong.

    MusicBrainz' search is a name search, not equality, so `Bush` answers with
    Kate Bush first - the id this page is here to correct. Every hit is shown
    with what MusicBrainz knows (type, country, years) so two bands of one name
    can be told apart.
    """
    here = _text(base) + "artist/%d" % artist["id"]
    rows = "".join(
        '<tr><td><strong>%s</strong>%s</td><td class="muted">%s</td>'
        '<td class="actions"><form method="post" action="%s">'
        '<input type="hidden" name="action" value="mbid">'
        '<input type="hidden" name="artist_mbid" value="%s">'
        '<button type="submit">Use this</button></form></td></tr>'
        % (_text(hit["name"]),
           ' <span class="muted">%s</span>' % _text(hit["disambiguation"])
           if hit["disambiguation"] else "",
           _text(" \u00b7 ".join(filter(None, [hit["kind"], hit["country"],
                                               hit["years"]]))),
           here, _text(hit["mbid"]))
        for hit in matches)
    body = ('<form method="get" action="%s/mbid">'
            '<label>Artist name<input name="q" value="%s" size="30"></label>'
            ' <button type="submit">Search MusicBrainz</button></form>%s%s'
            % (here, _text(query),
               ('<p class="banner bad">%s</p>' % _text(note)) if note else "",
               ('<table><thead><tr><th>Name</th><th>MusicBrainz says</th>'
                '<th></th></tr></thead><tbody>%s</tbody></table>' % rows
                if matches else
                '<p class="sub">Nothing matched. Try the act\'s full name, or '
                'paste its id from the MusicBrainz URL into the box on the '
                'artist page.</p>')))
    return ui.page(base, "MusicBrainz entry for " + artist["name"], body,
                   back="artist/%d" % artist["id"],
                   back_label="\u2190 Back to the artist")

