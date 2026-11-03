"""Title detail page: the title itself, its copies, and one button to play it."""

import html
import json

import ui

KIND_LABELS = {"movie": "Film", "tv": "TV", "music": "Music", "other": "Other"}

STORE_LABELS = {
    "itunes": "Apple TV",
    "appletv": "Apple TV",
    "rip": "ripped file",
    "jellyfin": "Jellyfin",
    "other": "other store",
}
def _adopt(store_rows):
    """Take the store list from the database for this render.

    `render()` is handed the rows because ui_title has no connection; every
    caller in the app shares one database, so a module-level mapping is the
    same for every request. Keys with no row keep the fallback wording above.
    """
    if not store_rows:
        return
    STORE_LABELS.update({row["key"]: row["label"] for row in store_rows})
    del STORE_CHOICES[:]
    STORE_CHOICES.extend((row["key"], row["label"]) for row in store_rows
                         if row["kind"] == "store" and row["is_active"])


STORE_CHOICES = []


def _text(value):
    if value is None:
        return ""
    return html.escape(str(value))


def _facts(row, base=""):
    """The one-line summary under the title: year, kind, artist, runtime ...

    Every part is escaped here, so the caller can drop the result into the page
    as it is. The artist is the one link in the line, because an album page is
    the obvious place to want the rest of that act's shelf - `base` is passed in
    so the link follows the add-on's own prefix.
    """
    bits = []
    if row["release_year"]:
        bits.append(_text(str(row["release_year"])))
    if row["kind"]:
        bits.append(_text(KIND_LABELS.get(row["kind"], row["kind"])))
    if "artist_name" in row.keys() and row["artist_name"]:
        name = _text(row["artist_name"])
        artist_id = (row["artist_id"] if "artist_id" in row.keys() else None)
        if base and artist_id:
            bits.append('<a href="%sartist/%d">%s</a>'
                        % (_text(base), artist_id, name))
        else:
            bits.append(name)
    if row["runtime_minutes"]:
        bits.append(_text("%d min" % row["runtime_minutes"]))
    if row["genres"]:
        bits.append(_text(row["genres"]))
    if row["rating"]:
        bits.append(_text("rated %s" % row["rating"]))
    return " · ".join(bits)


def _links(row):
    links = []
    if row["imdb_id"]:
        links.append('<a href="https://www.imdb.com/title/%s/" target="_blank"'
                     ' rel="noopener">IMDb %s</a>'
                     % (_text(row["imdb_id"]), _text(row["imdb_id"])))
    if row["tmdb_id"]:
        links.append('<a href="https://www.themoviedb.org/movie/%s"'
                     ' target="_blank" rel="noopener">TMDb %s</a>'
                     % (_text(row["tmdb_id"]), _text(row["tmdb_id"])))
    if row["musicbrainz_id"]:
        links.append('<a href="https://musicbrainz.org/release-group/%s"'
                     ' target="_blank" rel="noopener">MusicBrainz</a>'
                     % _text(row["musicbrainz_id"]))
    return " · ".join(links)


def _copy_title(item, labels, store=None, ripped=False):
    """'Blu-ray · Matt' - what a copy is, in one phrase."""
    if ripped and not store:
        bits = ["Ripped file"]              # a file with no disc or purchase
    else:
        bits = [labels.get(item["format"], item["format"] or "copy")]
        if store:
            bits.append(store)
        if ripped and "ripped" not in bits[0].lower():
            bits.append("ripped file")
    owner = item["owner_name"] or "unowned"
    if owner != FILES_OWNER:  # files belong to the shelf, not to a person
        bits.append(owner)
    return " · ".join(bits)


def _ripped(digital):
    """{item_id} for the copies that are a file on disk."""
    return {source["item_id"] for source in digital
            if source["provider"] == "rip"}


def _files(digital):
    """{item_id: path} so a ripped copy can say which file it is."""
    files = {}
    for source in digital:
        if source["provider"] == "rip" and source["path"]:
            files.setdefault(source["item_id"], source["path"])
    return files


def _stores(digital):
    """{item_id: 'iTunes'} for copies bought from a store rather than ripped."""
    found = {}
    for source in digital:
        provider = source["provider"]
        if provider in ("rip", "jellyfin") or not provider:
            continue
        found.setdefault(source["item_id"], STORE_LABELS.get(provider,
                                                            provider.title()))
    return found


def _badges(items, digital, labels, base=None, title_id=None):
    """One badge per copy; pressing one opens that copy on the Copies tab."""
    stores = _stores(digital)
    ripped = _ripped(digital)
    badges = []
    for item in items:
        text = _text(_copy_title(item, labels, stores.get(item["id"]),
                                 item["id"] in ripped))
        if base and title_id:
            badges.append('<a class="badge" href="%stitle/%d?tab=copies#copy-%d"'
                          ' title="Show this copy">%s</a>'
                          % (_text(base), title_id, item["id"], text))
        else:
            badges.append('<span class="badge">%s</span>' % text)
    return "".join(badges)


def _store_plays(base, row, shops):
    """One section per store that names a device to open it on.

    `shops` is [(store_row, target_row, app_name)] from stores.playable_stores().
    The button wakes the device and opens the app - a purchased title cannot be
    deep-linked, so the sheet says so rather than pretending otherwise.
    """
    if not shops:
        return ""
    parts = []
    for store, device, app in shops:
        device_name = _text(device.get("name") or "")
        app_name = _text(app or store["label"])
        parts.append(
            '<section class="store-play">\n'
            '  <h3>On the %s</h3>\n'
            '  <form method="post" action="%stitle/%s/store">\n'
            '    <input type="hidden" name="store" value="%s">\n'
            '    <button type="submit">Open %s</button>\n'
            '  </form>\n'
            '  <p class="muted">Wakes the %s and opens %s &#8212; pick the '
            'title there.</p>\n'
            '</section>' % (device_name, _text(base), row["id"],
                             _text(store["key"]), app_name, device_name,
                             app_name))
    return "\n".join(parts)


def _play_block(base, row, items, digital, players, last_player,
                jellyfin_link, jellyfin_configured, sessions, labels,
                shops=()):
    """Badges plus one Play button; the button opens every way to play."""
    playable = [source for source in digital
                if (source["provider"] == "rip" and source["path"]
                    and source["root_path"])
                or source["provider"] == "jellyfin"]
    jellyfin_ready = bool(jellyfin_link and jellyfin_configured)
    if not playable and not jellyfin_ready and not shops:
        return ('<p class="muted">No file to play yet - rip the disc to'
                ' a media folder and the scan picks it up by itself.</p>', "")

    if playable:
        player_options = "".join(
            '<option value="%s"%s>%s%s</option>'
            % (_text(player["entity_id"]),
               " selected" if player["entity_id"] == last_player else "",
               _text(player["name"]),
               " (%s)" % _text(player["state"]) if player["state"] else "")
            for player in players)
        def _label(source):
            kind = labels.get(source["copy_format"],
                              source["copy_format"] or "copy")
            owner = source["owner_name"] or "unowned"
            if owner != FILES_OWNER:  # files belong to the shelf, not a person
                kind = "%s \u00b7 %s" % (kind, owner)
            if source["provider"] == "jellyfin":
                parts_ = [x for x in ((source["notes"] or ""),
                                     (source["external_id"] or "")) if x]
                return "%s \u2014 Jellyfin%s" % (
                    _text(kind),
                    (" \u00b7 " + _text(" \u00b7 ".join(parts_)))
                    if parts_ else "")
            return "%s \u2014 %s \u00b7 %s" % (
                _text(kind),
                _text(source["root_label"] or "media"),
                _text(source["path"]))

        # a Jellyfin copy has no file to hand a player: it is played
        # through the server, in the section below
        playable = [src for src in playable
                    if src["provider"] != "jellyfin"
                    and (src["path"] or "").strip()]
        if not playable:
            copies = ""
        elif len(playable) == 1:
            source = playable[0]
            copies = ('<p class="copy-play"><input type="hidden"'
                      ' name="source_id" value="%d">'
                      '<button type="submit">Play</button>'
                      ' <span class="muted">%s</span></p>'
                      % (source["id"], _label(source)))
        else:
            copies = ('<p class="copy-play">Which file: '
                      '<select name="source_id" id="playfile">%s</select> '
                      '<button type="submit">Play</button></p>'
                      % "".join('<option value="%d">%s</option>'
                                % (source["id"], _label(source))
                                for source in playable))
        if not playable:
            shelf = ""
        else:
            shelf = ('<h3>From Media folder</h3>'
                     '<p class="sub">Home Assistant opens the file on a player'
                     ' you choose.</p>'
                     '<form method="post" action="%stitle/%d/play">'
                     '<label>Player<select name="entity_id">%s</select>'
                     '</label>%s</form>'
                     % (_text(base), row["id"], player_options, copies))
    else:
        shelf = ""

    music = (row["kind"] or "") == "music"
    play_button = (
        '<p><button type="submit">Play the album in Jellyfin</button>'
        '<span class="sub">The whole album queues, not just the first'
        ' track.</span></p>' if music
        else '<p><button type="submit">Play in Jellyfin</button></p>')
    picker = _version_picker(digital, items)
    if not jellyfin_ready:
        jellyfin = ""
    elif sessions:
        session_options = "".join(
            '<option value="%s">%s%s</option>'
            % (_text(session.get("id")),
               _text(session.get("name") or "device"),
               " \u00b7 " + _text(session["user"])
               if session.get("user") else "")
            for session in sessions)
        jellyfin = ('<h3>On the Jellyfin server</h3>'
                    '<p class="sub">Starts it on a Jellyfin app that is open'
                    ' right now.</p>'
                    '<form method="post" action="%stitle/%d/jellyfin"'
                    '>'
                    '<label>Jellyfin device<select name="session_id">%s'
                    '</select></label>'
                    '%s'
                    '%s'
                    '</form>' % (_text(base), row["id"],
                                          session_options, picker, play_button))
    else:
        jellyfin = ('<h3>On the Jellyfin server</h3>'
                    '<p class="muted">No Jellyfin app is open at the moment -'
                    ' start it on the device you want and press Play again.</p>')

    if not shelf and not jellyfin and not shops:
        return ("", "")     # nothing to play: no button, no sheet

    # The caller puts the button at the top of the page; the sheet is a
    # modal, so where its markup sits in the document does not matter.
    button = ('<p class="play-row"><button type="button" class="primary"'
              ' data-open="play">Play</button></p>')
    sheet = ("""<dialog id="play" class="sheet">
  <h2>Play %s</h2>
  %s
  %s
  %s
  <form method="dialog"><p><button type="submit">Close</button></p></form>
</dialog>""" % (_text(row["name"]), shelf, jellyfin, _store_plays(base, row, shops)))
    return button, sheet


def _format_options(formats, labels, chosen):
    return "".join('<option value="%s"%s>%s</option>'
                   % (_text(fmt), " selected" if fmt == chosen else "",
                      _text(labels.get(fmt, fmt)))
                   for fmt in formats)


def _store_options(chosen):
    """The store picker: a list you can pick from and type into.

    A datalist rather than a select, because a shop nobody has used yet must
    not need a code change - whatever is typed is matched against the store
    list on save and becomes a new row when it is not there. Blank means no
    store at all: a disc was not bought from a shop, so it must claim neither
    a purchase nor "not a purchase".
    """
    options = "".join('<option value="%s"></option>' % _text(name)
                      for _key, name in STORE_CHOICES)
    return ('<input name="store" list="store-choices" value="%s" '
            'placeholder="-"><datalist id="store-choices">%s</datalist>'
            % (_text(chosen or ""), options))


def _copy_fields(item, formats, labels, store):
    """The grid of inputs shared by the copy forms."""
    return """
  <div class="grid">
    <label>Format<select name="format">%s</select></label>
    <label>Store%s</label>
    <label>Edition<input name="edition" value="%s" placeholder="Extended"></label>
    <label>Region<input name="region" value="%s" placeholder="B"></label>
    <label>Location<input name="location" value="%s" placeholder="Shelf 2"></label>
    <label>Condition<input name="condition" value="%s" placeholder="good"></label>
    <label>Barcode<input name="upc_ean" value="%s"></label>
    <label>Price paid<input name="price_paid" value="%s" placeholder="12.99"></label>
    <label>Bought on<input name="acquired_on" type="date" value="%s"></label>
    <label>Last watched<input name="last_watched_on" type="date" value="%s"></label>
    <label>Visibility<select name="visibility">
      <option value="shared"%s>shared</option>
      <option value="private"%s>private</option></select></label>
    <label>Notes<input name="notes" value="%s"></label>
  </div>""" % (_format_options(formats, labels, item["format"]),
             _store_options(store),
             _text(item["edition"]), _text(item["region"]),
             _text(item["location"]), _text(item["condition"]),
             _text(item["upc_ean"]), _text(item["price_paid"]),
             _text(item["acquired_on"]), _text(item["last_watched_on"]),
             " selected" if item["visibility"] == "shared" else "",
             " selected" if item["visibility"] == "private" else "",
             _text(item["notes"]))


FILES_OWNER = "Ripped files"


def _copy_line(item, labels, store, ripped=False, path=None):
    """One line that says what the copy is and where it came from."""
    head = _copy_title(item, labels, store, ripped)
    bits = [path, item["edition"], item["location"], item["condition"]]
    bits = [value for value in bits if value]
    detail = " · ".join(_text(bit) for bit in bits)
    money = []
    if item["price_paid"]:
        money.append(_text(item["price_paid"]))
    if item["acquired_on"]:
        money.append("bought %s" % _text(item["acquired_on"]))
    if money:
        detail = (detail + " · " if detail else "") + " ".join(money)
    watched = ""           # watched lives on the title, not on a copy
    detail = (detail + " · " if detail else "") + watched
    return '<strong>%s</strong> <span class="muted">%s</span>' % (head, detail)


def _delete_block(base, row):
    """The way out of a title that should never have existed: one button that
    takes the title and every copy on it, file-backed or Jellyfin."""
    return ('<form method="post" action="%stitle/%d/delete" class="sub"'
            ' onsubmit="return confirm(\'Delete this title and every copy on'
            ' it?\')">'
            '<button type="submit">Delete this title</button>'
            ' <span class="muted">with every copy on it, file or Jellyfin.'
            '</span></form>' % (html.escape(base), row["id"]))


def _copies_tab(base, row, items, digital, formats, labels, users,
                 viewer_id=None):
    parts = []
    stores = _stores(digital)
    files = _files(digital)

    def _is_file(item):
        # the ripped line is the copy a player uses: it goes under the button
        return item["format"] == "ripped"

    def _row_html(item):
        if item["format"] == "ripped":
            # a file is the importer's business: where it is, nothing to edit
            where = item["rip_path"] or files.get(item["id"]) or ""
            if not where:   # a Jellyfin copy has a route, not a path
                for source in digital:
                    if source["item_id"] != item["id"]:
                        continue
                    if source["provider"] != "jellyfin":
                        continue
                    # the match list below names the server and the id;
                    # here only the version is worth repeating
                    where = "Jellyfin"
                    if item["edition"]:
                        where += " \u00b7 " + item["edition"]
                    break
            if where.startswith("Jellyfin"):
                # the Jellyfin matches list below is what presents this
                # copy: a second line saying so is just noise
                return ""
            jf = ""
            for source in digital:
                if source["item_id"] == item["id"] and "jf_id" in source.keys():
                    jf = source["jf_id"] or ""
                    if jf:
                        break
            return ('<p class="copy-line"><strong>Ripped file</strong>'
                    ' <span class="muted">%s%s</span></p>'
                    % (_text(where),
                       " \u00b7 Jellyfin " + _text(jf) if jf else ""))
        store = stores.get(item["id"])
        remove = ('<form method="post" action="%sitems/%d/delete"'
                  " onsubmit=\"return confirm('Remove this copy?')\">"
                  '<p><button type="submit" class="danger">Delete copy'
                  '</button> <span class="muted">Removes this copy from the'
                  " catalogue: files and Jellyfin stay put; only the last"
                  " copy takes the title with it.</span></p></form>"
                  % (_text(base), item["id"]))
        return (
            '<details class="copy" id="copy-%d"><summary>%s</summary>'
            '<form method="post" action="%sitems/%d">%s'
            '<p><button type="submit">Save copy</button></p></form>%s'
            '</details>'
            % (item["id"], _copy_line(item, labels, store,
                                      item["id"] in _ripped(digital),
                                      (item["rip_path"]
                                       if "rip_path" in item.keys() else None)
                                      or files.get(item["id"])),
               _text(base),
               item["id"], _copy_fields(item, formats, labels, store),
               remove))

    if not items:
        parts.append('<p class="muted">No copies yet.</p>')
    for item in [c for c in items if not _is_file(c)]:
        parts.append(_row_html(item))
    parts.append('<p><button type="button" onclick="addcopy.showModal()">'
                 '+ Add a copy</button></p>')
    for item in [c for c in items if _is_file(c)]:
        parts.append(_row_html(item))

    owner_options = "".join('<option value="%d"%s>%s</option>'
                            % (user["id"],
                               " selected" if items and
                               user["display_name"] == items[0]["owner_name"]
                               else "", _text(user["display_name"]))
                            for user in users)
    blank = {"format": "cd" if row["kind"] == "music" else "bluray",
             "edition": None, "region": None, "location": None,
             "condition": None, "upc_ean": None, "price_paid": None,
             "acquired_on": None, "last_watched_on": None,
             "visibility": "shared", "notes": None}
    parts.append(
        '<dialog id="addcopy"><h2>Add a copy</h2>'
        '<form method="post" action="%sitems">'
        '<input type="hidden" name="title" value="%s">'
        '<input type="hidden" name="kind" value="%s">'
        '<input type="hidden" name="release_year" value="%s">'
        '<input type="hidden" name="from" value="title">'
        '<div class="grid"><label>Owner<select name="owner_id">%s</select>'
        '</label></div>%s'
        '<p><button type="submit">Add copy</button>'
        ' <button type="button" onclick="addcopy.close()">Cancel</button></p>'
        '</form></dialog>'
        % (_text(base), _text(row["name"]), _text(row["kind"]),
           _text(row["release_year"] or ""), owner_options,
           _copy_fields(blank, formats, labels, "")))
    parts.append('<script>var target = document.querySelector("details.copy:target");'
                 'if (target) { target.open = true; }</script>')
    routes = [s for s in digital if s["provider"] == "jellyfin"]
    if routes:
        parts.append(
            '<h3>Jellyfin matches</h3><ul class="routes">%s</ul>'
            % "".join(
                '<li><strong>%s</strong> <span class="muted">%s</span>'
                ' <form method="post" action="%stitle/%d/unlink"'
                ' style="display:inline">'
                '<input type="hidden" name="source_id" value="%d">'
                '<button type="submit">Unlink</button></form></li>'
                % (_text(s["jf_name"] or s["notes"] or "Jellyfin"),
                   _text(" · ".join(x for x in (s["jf_kind"],
                                                s["jf_path"],
                                                s["external_id"]) if x)),
                   _text(base), row["id"], s["id"])
                for s in routes))
    parts.append('<p class="sub"><a href="%stitle/%d/link">Link a Jellyfin item by hand</a></p>'
                 % (_text(base), row["id"]))
    return "".join(parts)


def _title_form(base, row):
    kinds = (("movie", "Film"), ("tv", "TV"), ("music", "Music"),
             ("other", "Other"))
    options = "".join('<option value="%s"%s>%s</option>'
                      % (kind, " selected" if kind == row["kind"] else "",
                         _text(label))
                      for kind, label in kinds)
    if row["kind"] == "music":
        ids = ('\n    <label>MusicBrainz ID<input name="musicbrainz_id"'
               ' value="%s" placeholder="a8fb6ac6-..."></label>'
               % _text(row["musicbrainz_id"]))
    else:
        ids = ('\n    <label>IMDb ID<input name="imdb_id" value="%s">'
               '</label>\n    <label>TMDb ID<input name="tmdb_id" value="%s">'
               '</label>' % (_text(row["imdb_id"]), _text(row["tmdb_id"] or "")))
    return """
<h2>Title</h2>
<form method="post" action="%stitle/%d">
  <div class="grid">
    <label>Name<input name="name" value="%s"></label>
    <label>Artist<input name="artist" value="%s"></label>
    <label>Year<input name="release_year" value="%s"></label>
    <label>Kind<select name="kind">%s</select></label>%s
  </div>
  <p><button type="submit">Save title</button></p>
</form>""" % (_text(base), row["id"], _text(row["name"]),
             _text(row["artist_name"] if "artist_name" in row.keys()
                   else ""), _text(row["release_year"] or ""),
             options, ids)


def _art_block(base, row, jellyfin_link):
    """The pencil on the cover: which image the shelf shows, and forgetting one.

    It used to be a paragraph-wide *Choose cover art* button under the picture,
    taking as much room as the picture itself. It is now a small pencil on the
    corner of the cover, and the sheet it opens offers two things the old one
    did not: a way to forget an image that is wrong (a poster fetched for the
    wrong film hangs around in art_candidates forever) and a way back to the
    automatic choice.
    """
    picks = []
    if jellyfin_link:
        picks.append(("jellyfin", base + "art/" + jellyfin_link["jf_id"],
                      "Jellyfin still"))
    try:
        for item in json.loads(row["art_candidates"] or "[]"):
            url = item.get("url")
            if url and not any(pick[1] == url for pick in picks):
                picks.append(("poster", url, item.get("source") or "poster"))
    except (ValueError, AttributeError):
        pass
    if row["poster_url"] and not any(pick[1] == row["poster_url"]
                                     for pick in picks):
        picks.append(("poster", row["poster_url"],
                      row["metadata_source"] or "poster"))
    if not picks:                    # nothing to choose between
        return ""
    current = ui.pick_cover(base,
                            jellyfin_link["jf_id"] if jellyfin_link else None,
                            row["poster_url"], row["art_source"] or "")
    cells = "".join(
        '<form class="artpick%s" method="post" action="%stitle/%d/art">'
        '<input type="hidden" name="source" value="%s">'
        '<input type="hidden" name="url" value="%s">'
        '<button type="submit" name="action" value="use">'
        '<img src="%s" alt="" loading="lazy"><span>%s</span></button>'
        '<button type="submit" name="action" value="forget" class="forget"'
        ' title="Forget this image" aria-label="Forget this image">\u00d7'
        '</button></form>'
        % (" current" if url == current else "", _text(base), row["id"],
           _text(kind), _text(url), _text(url), _text(label))
        for kind, url, label in picks)
    automatic = (
        '<form class="artpick%s" method="post" action="%stitle/%d/art">'
        '<input type="hidden" name="action" value="auto">'
        '<button type="submit"><span class="auto">Automatic</span>'
        '<span>Jellyfin first, then the poster</span></button></form>'
        % ("" if (row["art_source"] or "") else " current",
           _text(base), row["id"]))
    pencil = ('<button type="button" class="coveredit" data-open="artpick"'
              ' title="Choose the cover image"'
              ' aria-label="Choose the cover image">\u270e</button>')
    return (pencil
            + '<dialog id="artpick"><h2>Cover art</h2>'
            '<p class="sub">Pick the image the shelf shows for %s.</p>'
            '<div class="artpicks">%s%s</div>'
            '<form method="dialog"><p><button type="submit">Close</button></p>'
            '</form></dialog>'
            % (_text(row["name"]), cells, automatic))


def render(base, row, items, digital, formats, labels, summary=None, error=None,
           ripped_root="", roots=(), owner=None, players=(), last_player=None,
           jellyfin_link=None, jellyfin_sessions=(),
           jellyfin_configured=False, tab="about", users=(), viewer_id=None,
           back=None, stores=None, shops=(),
           back_label="\u2190 Back to the catalogue"):
    _adopt(stores)
    parts = []
    if summary:
        parts.append('<p class="banner good">%s</p>' % _text(summary))
    if error:
        parts.append('<p class="banner bad">%s</p>' % _text(error))

    cover = ui.pick_cover(base, jellyfin_link["jf_id"] if jellyfin_link else None,
                          row["poster_url"], row["art_source"] or "")
    # the pencil sits on the cover, so both live in one box - and a title with
    # no image at all gets neither (a 170x245 grey box is not an improvement)
    if cover:
        parts.append('<div class="coverwrap">%s%s</div>'
                     % ('<img class="cover" src="%s" alt="" loading="lazy">'
                        % _text(cover),
                        _art_block(base, row, jellyfin_link)))

    facts = _facts(row, base)
    if facts:
        parts.append('<p class="sub">%s</p>' % facts)

    # The Play button sits at the top: it used to be below the copies, so
    # starting a film meant scrolling past every copy of it first.
    play_button, play_sheet = _play_block(
        base, row, items, digital, players, last_player, jellyfin_link,
        jellyfin_configured, jellyfin_sessions, labels, shops=shops)
    if play_button:
        parts.append(play_button)
    links = _links(row)
    if links:
        parts.append('<p class="sub">%s</p>' % links)
    if row["metadata_updated_at"]:
        parts.append('<p class="sub">Updated %s%s</p>'
                     % (_text(row["metadata_updated_at"]),
                        " via " + _text(row["metadata_source"])
                        if row["metadata_source"] else ""))
    if row["overview"]:
        parts.append('<p>%s</p>' % _text(row["overview"]))

    parts.append('<details class="copy"><summary>Edit metadata</summary>')
    parts.append(_title_form(base, row))
    parts.append('<form method="post" action="%stitle/%d/metadata">'
                 '<p><button type="submit">Refresh metadata</button></p>'
                 '</form>' % (_text(base), row["id"]))
    parts.append('</details>')
    # the copies belong on the title page itself, not behind a tab
    copies_on = True
    tabs = ""
    parts.append(tabs)

    if True:   # the copies sit below; this branch is no longer exclusive
        parts.append('<h2>Copies</h2>')
        parts.append(_copies_tab(base, row, items, digital, formats, labels,
                                 users, viewer_id))
        parts.append(_delete_block(base, row))
    parts.append(play_sheet)

    if back is None:
        back = ("?kind=%s" % row["kind"]
                + ("&owner=%d" % owner if owner else ""))
    return ui.page(base, row["name"], "".join(parts), back=back,
                   back_label=back_label)


def _version_picker(digital, items):
    """Which file of a multi-version Jellyfin item should start."""
    copies = [source for source in digital
              if source["provider"] == "jellyfin"]
    editions = {item["id"]: (item["edition"] or "").strip() for item in items}
    if not copies:
        return '<input type="hidden" name="source_id" value="">'
    if len(copies) == 1:
        return ('<input type="hidden" name="source_id" value="%d">'
                % copies[0]["id"])
    return ('<label>Version<select name="source_id">%s</select></label>'
            % "".join('<option value="%d">%s</option>'
                      % (source["id"],
                         _text(editions.get(source["item_id"]) or "default"))
                      for source in copies))
