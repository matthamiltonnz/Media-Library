"""The collection pages: every series on the shelf, and one series.

Same shape as `ui_artists`: the list is a wall of covers with a search box,
and one collection is a cover, a paragraph about the series, and the films in
the order they are watched, numbered. The difference is the box at the bottom
that puts a film into the collection - that is what a search cannot do.
"""

import html
import time
import urllib.parse

import ui


def _text(value):
    if value is None:
        return ""
    return html.escape(str(value))


def _years(row):
    first = row["first_year"] if "first_year" in row.keys() else None
    last = row["last_year"] if "last_year" in row.keys() else None
    if first and last and first != last:
        return "%d\u2013%d" % (first, last)
    if first or last:
        return str(first or last)
    return ""


def _cover(base, jf_id, poster_url):
    """Jellyfin's picture when a copy is linked, else the stored poster."""
    if jf_id:
        return base + "art/" + jf_id
    return ui.cover_url(base, poster_url)


def _formats(row, labels):
    """The copies on the shelf, with a play mark instead of the word "ripped".

    "Ripped" here means "there is a route to play it", the same reading the
    catalogue's grid uses - not the "is this a file?" question.
    """
    raw = [fmt for fmt in (row["formats"] or "").split(",") if fmt]
    shown = [labels.get(fmt, fmt) for fmt in raw if fmt != "ripped"]
    text = ", ".join(shown)
    if "ripped" in raw:
        text = (text + " \u00b7 " if text else "") + "\u25b6"
    return text


def _caption(row, labels, number=None):
    bits = []
    if number:
        bits.append("#%d" % number)
    if row["release_year"]:
        bits.append(str(row["release_year"]))
    formats = _formats(row, labels)
    if formats:
        bits.append(formats)
    return " \u00b7 ".join(bits)


def _thumb(base, jf_id, poster_url, stack=False):
    cls = "thumb stack" if stack else "thumb"
    cover = _cover(base, jf_id, poster_url)
    if not cover:
        return '<div class="%s"><span class="muted">no cover</span></div>' % cls
    return ('<div class="%s"><img src="%s" alt="" loading="lazy"></div>'
            % (cls, _text(cover)))


def render_list(base, rows, query=""):
    """Every collection with something on the shelf."""
    back = "?kind=%s&view=grid" % ("tv" if rows and all(
        (row["kind"] or "") == "tv" for row in rows) else "movie")
    search = ('<form class="search" method="get" action="%scollections">'
              '<input name="q" value="%s" placeholder="Search collections">'
              '<button type="submit">Search</button></form>'
              % (_text(base), _text(query)))
    add = ('<details class="add"><summary>Add a collection</summary>'
           '<form method="post" action="%scollections">'
           '<label>Name<input name="name" placeholder="Fast &amp; Furious"'
           ' required></label>'
           ' <button type="submit">Add this collection</button>'
           '</form></details>' % _text(base))

    if not rows:
        empty = ("<p>No collection matches <strong>%s</strong>.</p>"
                 % _text(query) if query else
                 "<p>No collections yet. A collection is a film series - Fast "
                 "&amp; Furious, Alien, the Cornetto trilogy - and it lives on "
                 "each film's page under <em>Collection</em>. Make one below, "
                 "or from any film.</p>")
        return ui.page(base, "Collections", search + empty + add, back=back)

    cards = "".join(
        '<a class="card" href="%scollection/%d%s">%s'
        '<div class="cap"><strong>%s</strong>'
        '<span class="muted">%s</span></div></a>'
        % (_text(base), row["id"],
           "?q=" + urllib.parse.quote(query) if query else "",
           _thumb(base, row["jf_id"], row["poster_url"] or row["member_poster"],
                  stack=True),
           _text(row["name"]),
           _text(" \u00b7 ".join(filter(None, [
               "%d film%s" % (row["films"], "" if row["films"] == 1 else "s"),
               _years(row)]))))
        for row in rows)
    count = ('<p class="sub">%d collection%s%s</p>'
             % (len(rows), "" if len(rows) == 1 else "s",
                ' matching \u201c%s\u201d' % _text(query) if query else ""))
    return ui.page(base, "Collections",
                   search + count + '<div class="cards">%s</div>' % cards + add,
                   back=back)


def _series_block(base, row, series):
    """What the series holds that the shelf does not, and the button that asks.

    Wikidata files each film under its series (`P179`), so pointing at the
    series item the other way round lists every film in it - including the ones
    nobody here owns. That answer is kept in `collection_members`, so a page
    never waits on Wikimedia; the button is what fetches it.
    """
    series = series or {}
    count = series.get("count") or 0
    missing = series.get("missing") or []
    elsewhere = series.get("elsewhere") or []
    label = "Look it up again" if count else "Look up the films in this series"
    button = ('<form class="inline" method="post"'
              ' action="%scollection/%d/members">'
              '<button type="submit">%s</button></form>'
              % (_text(base), row["id"], label))
    head = "<h3>Films in this series</h3>"
    if not count:
        return (head + '<p class="sub">Wikidata lists what is in a film series, '
                'so asking it here shows the films this shelf is missing. Two '
                'requests, and the answer is kept.</p>' + button)

    def link(entry):
        if entry.get("imdb_id"):
            return "https://www.imdb.com/title/%s/" % entry["imdb_id"]
        return "https://www.wikidata.org/wiki/%s" % entry["qid"]

    def said(entry):
        """The year, and a word for a film nobody could own yet."""
        if not entry.get("year"):
            return ""
        text = str(entry["year"])
        if int(entry["year"]) > time.localtime().tm_year:
            text += " \u00b7 not out yet"
        return text

    bits = [head]
    if missing:
        bits.append('<p class="sub">%d of the %d films Wikidata files under this '
                    'series %s not on the shelf:</p>'
                    % (len(missing), count,
                       "are" if len(missing) > 1 else "is"))
        bits.append('<ul class="gaps">%s</ul>' % "".join(
            '<li><a href="%s" target="_blank" rel="noopener">%s</a>%s</li>'
            % (_text(link(entry)), _text(entry["name"]),
               '<span class="muted"> %s</span>' % _text(said(entry))
               if said(entry) else "")
            for entry in missing))
    else:
        bits.append('<p class="sub">All %d films Wikidata files under this '
                    'series are on the shelf.</p>' % count)
    if elsewhere:
        bits.append('<p class="sub">Also in the series, kept under another '
                    'collection: %s</p>' % " \u00b7 ".join(
                        '<a href="%stitle/%d">%s</a>'
                        % (_text(base), entry["title_id"], _text(entry["name"]))
                        for entry in elsewhere))
    checked = series.get("checked") or ""
    plural = "" if count == 1 else "s"
    if series.get("qid"):
        # the series' own record: its description, its other links, its history
        known = ('Wikidata knows <a href="https://www.wikidata.org/wiki/%s"'
                 ' target="_blank" rel="noopener">%d film%s in this series</a>'
                 % (_text(series["qid"]), count, plural))
    else:
        known = "Wikidata knows %d film%s in this series" % (count, plural)
    bits.append('<p class="sub">%s%s.</p>'
                % (known, ", checked " + _text(checked) if checked else ""))
    bits.append(button)
    return "".join(bits)



def render_collection(base, row, titles, labels, notice="", bad=False,
                      found=None, find="", query="", series=None):
    """One collection: what it is, and every film in it, numbered."""
    # a film opened from a filtered collection comes back to that filter, so the
    # page's own search rides along in `from` (0.23.166)
    target = "collection/%d" % row["id"]
    if query:
        target += "?q=" + urllib.parse.quote(query)
    here = _text(urllib.parse.quote(target, safe=""))
    own = row["poster_url"] if "poster_url" in row.keys() else ""
    member = row["member_poster"] if "member_poster" in row.keys() else ""
    poster = _cover(base, row["jf_id"] if "jf_id" in row.keys() else None,
                    own or member)
    years = [t["release_year"] for t in titles if t["release_year"]]
    info = " \u00b7 ".join(filter(None, [
        "%d film%s" % (len(titles), "" if len(titles) == 1 else "s"),
        _years({"first_year": min(years) if years else None,
                "last_year": max(years) if years else None}),
        "TV" if (row["kind"] or "") == "tv" else ""]))

    about = (row["overview"] or "").strip()
    picture = ('<img class="portrait" src="%s" alt="">' % _text(poster)
               if poster else "")
    body = ('<div class="bio">%s<div><p>%s</p></div></div>'
            % (picture, _text(about) if about else
               '<em class="muted">Say what this series is, below.</em>'))

    # The page's own search. The catalogue's criteria travel into a collection
    # (`?q=`), so a card clicked out of a filtered catalogue lands on the series
    # with the same word applied to its films - not to the add-a-film box, which
    # is a different question and has its own field (`?find=`).
    shown = titles
    if query:
        needle = query.lower()
        shown = [t for t in titles if needle in (t["name"] or "").lower()]
    numbers = dict((t["id"], n) for n, t in enumerate(titles, 1))
    cards = "".join(
        '<a class="card" href="%stitle/%d?from=%s">%s'
        '<div class="cap"><strong>%s</strong>'
        '<span class="muted">%s</span></div></a>'
        % (_text(base), title["id"], here,
           _thumb(base, title["jf_id"], title["poster_url"]),
           _text(title["name"]),
           _text(_caption(title, labels, numbers.get(title["id"]))))
        for title in shown)
    if cards:
        shelf = '<div class="cards">%s</div>' % cards
    elif query:
        shelf = ('<p>No film in this collection matches \u201c%s\u201d. '
                 '<a href="%scollection/%d">Show all %d</a></p>'
                 % (_text(query), _text(base), row["id"], len(titles)))
    else:
        shelf = "<p>Nothing in this collection yet - add a film below.</p>"

    search = ('<form class="search" method="get" action="%scollection/%d">'
              '<input name="q" value="%s" placeholder="Search this collection">'
              '<button type="submit">Search</button></form>'
              % (_text(base), row["id"], _text(query)))
    if query:
        # "showing N of M films matching" - a verb would have to agree with the
        # count that is not shown, which is how "0 of 1 film match" happened
        note = ('<p class="sub">Showing %d of %d films matching \u201c%s\u201d '
                '\u00b7 <a href="%scollection/%d">clear the search</a></p>'
                % (len(shown), len(titles), _text(query),
                   _text(base), row["id"]))
    else:
        note = ""


    edit = ('<details class="copy"><summary>Edit this collection</summary>'
            '<form method="post" action="%scollection/%d">'
            '<div class="grid">'
            '<label>Name<input name="name" value="%s"></label>'
            '<label>What the series is<textarea name="notes" rows="3">%s'
            '</textarea></label>'
            '</div>'
            '<p><button type="submit">Save the collection</button></p>'
            '</form>'
            '<form method="post" action="%scollection/%d">'
            '<input type="hidden" name="delete" value="1">'
            '<p><button type="submit" class="danger">Delete this collection'
            '</button> <span class="muted">the films stay on the shelf</span>'
            '</p></form></details>'
            % (_text(base), row["id"], _text(row["name"]),
               _text(row["overview"] or ""), _text(base), row["id"]))

    # A collection is a name and a paragraph until somebody types one, and a
    # series is the one kind of thing a catalogue normally has no description
    # for. This asks its Wikipedia article for both - and never over what is
    # already there.
    refresh = ('<form class="inline" method="post"'
               ' action="%scollection/%d/metadata">'
               '<button type="submit">Refresh metadata</button></form>'
               '<p class="sub">Fills the paragraph and the cover above from the '
               "series' Wikipedia article, where they are empty - a paragraph "
               'you typed is never replaced.</p>'
               % (_text(base), row["id"]))

    lookup = ('<form class="search" method="get" action="%scollection/%d">'
              '<input name="find" value="%s" placeholder="A word from its name">'
              '%s<button type="submit">Search</button></form>'
              % (_text(base), row["id"], _text(find),
                 '<input type="hidden" name="q" value="%s">' % _text(query)
                 if query else ""))
    if found:
        hits = "".join(
            '<tr><td><a href="%stitle/%d">%s</a></td>'
            '<td class="muted">%s</td><td class="muted">%s</td>'
            '<td class="actions"><form method="post"'
            ' action="%scollection/%d/add">'
            '<input type="hidden" name="title_id" value="%d">'
            '<button type="submit">Add</button></form></td></tr>'
            % (_text(base), hit["id"], _text(hit["name"]),
               _text(hit["release_year"] or ""),
               _text("in %s" % hit["in_collection"] if hit["in_collection"]
                     else "%d cop%s" % (hit["copies"],
                                        "y" if hit["copies"] == 1 else "ies")),
               _text(base), row["id"], hit["id"])
            for hit in found)
        results = ('<table><tr><th>Title</th><th>Year</th><th>On the shelf</th>'
                   '<th></th></tr>%s</table>' % hits)
    elif find:
        results = ('<p>Nothing on the shelf matches <strong>%s</strong>.</p>'
                   % _text(find))
    else:
        results = ("<p class=\"sub\">Type a word from the film's name - the odd "
                   "one out of a series is the hard one to find.</p>")
    # behind a button: the page is for reading the collection, and a search box
    # sitting under it looks like the page's own search - which it now has. It
    # opens itself only when a `?find=` came back, i.e. somebody searched it.
    add = ('<details class="add"%s>'
           '<summary>Add a film to this collection</summary>%s%s</details>'
           % (" open" if find else "", lookup, results))

    banner = ('<p class="banner %s">%s</p>'
              % ("bad" if bad else "good", _text(notice)) if notice else "")
    head = ('<p class="sub"><a href="%scollections">All collections</a>%s</p>'
            % (_text(base), " \u00b7 " + _text(info) if info else ""))
    # opened out of a catalogue search -> back to that search; otherwise back to
    # the list of collections
    if query:
        back = "?q=" + urllib.parse.quote(query)
        back_label = "\u2190 Back to the catalogue"
    else:
        back = "collections"
        back_label = "\u2190 Back to the collections"
    return ui.page(base, row["name"],
                   banner + head + body + refresh + edit + search + note + shelf +
                   _series_block(base, row, series) + add,
                   back=back, back_label=back_label)

