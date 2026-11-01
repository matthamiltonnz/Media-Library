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
PURCHASE_STORES = ("itunes", "other")


def _text(value):
    if value is None:
        return ""
    return html.escape(str(value))


def _facts(row):
    bits = []
    if row["release_year"]:
        bits.append(str(row["release_year"]))
    if row["kind"]:
        bits.append(KIND_LABELS.get(row["kind"], row["kind"]))
    if row["runtime_minutes"]:
        bits.append("%d min" % row["runtime_minutes"])
    if row["genres"]:
        bits.append(row["genres"])
    if row["rating"]:
        bits.append("rated %s" % row["rating"])
    return " · ".join(_text(bit) for bit in bits)


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
    """{item_id} for the copies that are a file on the NAS."""
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


def _play_block(base, row, items, digital, players, last_player,
                jellyfin_link, jellyfin_configured, sessions, labels):
    """Badges plus one Play button; the button opens every way to play."""
    playable = [source for source in digital
                if source["provider"] == "rip" and source["path"]
                and source["root_path"]]
    jellyfin_ready = bool(jellyfin_link and jellyfin_configured)
    badges = ""  # the copies list below replaces these pills
    badges = _badges(items, digital, labels, base, row["id"])
    head = ('<div class="badges">%s</div>' % badges if badges
            else '<p class="muted">No copies recorded yet.</p>')
    if not playable and not jellyfin_ready:
        return (head + '<p class="muted">No file to play yet - rip the disc to'
                ' the NAS and the scan picks it up by itself.</p>')

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
            return "%s \u2014 %s \u00b7 %s" % (
                _text(kind),
                _text(source["root_label"] or "media"),
                _text(source["path"]))

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
        shelf = ('<h3>From the shelf</h3>'
                 '<p class="sub">Home Assistant opens the file on a player you'
                 ' choose.</p>'
                 '<form method="post" action="%stitle/%d/play">'
                 '<label>Player<select name="entity_id">%s</select></label>%s'
                 '</form>' % (_text(base), row["id"], player_options, copies))
    else:
        shelf = ('<h3>From the shelf</h3><p class="muted">No ripped file for'
                 ' this title yet - rip the disc and the scan picks it up.</p>')

    if not jellyfin_ready:
        jellyfin = ""
    elif sessions:
        session_options = "".join(
            '<option value="%s">%s%s</option>'
            % (_text(session.get("id")), _text(session.get("name") or "device"),
               " — " + _text(session.get("client")) if session.get("client")
               else "")
            for session in sessions)
        jellyfin = ('<h3>On the Jellyfin server</h3>'
                    '<p class="sub">Starts it on a Jellyfin app that is open'
                    ' right now.</p>'
                    '<form method="post" action="%stitle/%d/jellyfin"'
                    ' onsubmit="var f=document.getElementById(\'playfile\');'
                    'this.source_id.value=f?f.value:\'\'">'
                    '<input type="hidden" name="source_id" value="">'
                    '<label>Jellyfin device<select name="session_id">%s'
                    '</select></label>'
                    '<p><button type="submit">Play in Jellyfin</button></p>'
                    '</form>' % (_text(base), row["id"], session_options))
    else:
        jellyfin = ('<h3>On the Jellyfin server</h3>'
                    '<p class="muted">No Jellyfin app is open at the moment -'
                    ' start it on the device you want and press Play again.</p>')

    return """%s
<p><button type="button" class="primary" data-open="play">Play</button></p>
<dialog id="play" class="sheet">
  <h2>Play %s</h2>
  %s
  %s
  <form method="dialog"><p><button type="submit">Close</button></p></form>
</dialog>
<script>
document.addEventListener("click", function (event) {
  var opener = event.target.closest("[data-open]");
  if (opener) {
    document.getElementById(opener.dataset.open).showModal();
  }
});
</script>""" % (head, _text(row["name"]), shelf, jellyfin)


def _format_options(formats, labels, chosen):
    return "".join('<option value="%s"%s>%s</option>'
                   % (_text(fmt), " selected" if fmt == chosen else "",
                      _text(labels.get(fmt, fmt)))
                   for fmt in formats)


def _store_options(chosen):
    options = ['<option value=""%s>not a purchase</option>'
               % ("" if chosen else " selected")]
    for store in PURCHASE_STORES:
        options.append('<option value="%s"%s>%s</option>'
                       % (store, " selected" if store == chosen else "",
                          _text(STORE_LABELS[store])))
    return "".join(options)


def _copy_fields(item, formats, labels, store):
    """The grid of inputs shared by the copy forms."""
    return """
  <div class="grid">
    <label>Format<select name="format">%s</select></label>
    <label>Store<select name="store">%s</select></label>
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
    watched = ("watched %s" % _text(item["last_watched_on"])
               if item["last_watched_on"] else "not watched")
    detail = (detail + " · " if detail else "") + watched
    return '<strong>%s</strong> <span class="muted">%s</span>' % (head, detail)


def _copies_tab(base, row, items, digital, formats, labels, users,
                 viewer_id=None):
    parts = []
    stores = _stores(digital)
    files = _files(digital)
    if not items:
        parts.append('<p class="muted">No copies yet.</p>')
    for item in items:
        store = stores.get(item["id"])
        if True:      # any household member may remove a copy, not just its owner
            remove = ('<form method="post" action="%sitems/%d/delete"'
                      " onsubmit=\"return confirm('Remove this copy?')\">"
                      '<p><button type="submit" class="danger">Delete copy'
                      '</button> <span class="muted">Drops the copy, its file'"'"'s'
                      " placeholder and any Jellyfin link.</span></p></form>"
                      % (_text(base), item["id"]))
        else:
            remove = ('<p class="muted">Shared copy - only %s can delete'
                      ' it.</p>' % _text(item["owner_name"] or "its owner"))
        parts.append(
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
        '<p><button type="button" onclick="addcopy.showModal()">+ Add a copy</button></p>'
        '<dialog id="addcopy"><h2>Add a copy</h2>'
        '<form method="post" action="%sitems">'
        '<input type="hidden" name="title" value="%s">'
        '<label>Media type<select name="kindpick"'
        ' onchange="this.form.kind.value=this.value">'
        '<option value="movie">Film</option>'
        '<option value="tv">TV</option>'
        '<option value="music">Music</option>'
        '</select></label>'
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
    <label>Year<input name="release_year" value="%s"></label>
    <label>Kind<select name="kind">%s</select></label>%s
  </div>
  <p><button type="submit">Save title</button></p>
</form>""" % (_text(base), row["id"], _text(row["name"]),
             _text(row["release_year"] or ""), options, ids)


def _art_block(base, row, jellyfin_link):
    """Pick which image the shelf shows: the Jellyfin still or a fetched poster."""
    picks = []
    if jellyfin_link:
        picks.append(("jellyfin", base + "art/" + jellyfin_link["jf_id"], "Jellyfin"))
    try:
        for item in json.loads(row["art_candidates"] or "[]"):
            url = item.get("url")
            if url:
                picks.append(("poster", url, item.get("source") or "lookup"))
    except (ValueError, AttributeError):
        pass
    if row["poster_url"] and not any(p[1] == row["poster_url"] for p in picks):
        picks.append(("poster", row["poster_url"],
                      row["metadata_source"] or "poster"))
    if len(picks) < 2:                   # nothing to choose between
        return ""
    current = ui.pick_cover(base, jellyfin_link["jf_id"] if jellyfin_link else None,
                            row["poster_url"], row["art_source"] or "")
    cells = "".join(
        '<form class="artpick%s" method="post" action="%s">'
        '<input type="hidden" name="source" value="%s">'
        '<input type="hidden" name="url" value="%s">'
        '<button type="submit"><img src="%s" alt="" loading="lazy"></button>'
        '<span>%s</span></form>'
        % (" current" if url == current else "",
           _text("%stitle/%d/art" % (base, row["id"])),
           _text(kind), _text(url), _text(url), _text(label))
        for kind, url, label in picks)
    return ('<p><button type="button" onclick="artpick.showModal()">'
            'Choose cover art</button></p>'
            '<dialog id="artpick"><h2>Cover art</h2>'
            '<p class="sub">Pick the image the shelf shows for this title.</p>'
            '<div class="artpicks">%s</div>'
            '<p><button type="button" onclick="artpick.close()">Close</button></p>'
            '</dialog>' % cells)


def render(base, row, items, digital, formats, labels, summary=None, error=None,
           ripped_root="", roots=(), owner=None, players=(), last_player=None,
           jellyfin_link=None, jellyfin_sessions=(),
           jellyfin_configured=False, tab="about", users=(), viewer_id=None,
           back=None):
    parts = []
    if summary:
        parts.append('<p class="banner good">%s</p>' % _text(summary))
    if error:
        parts.append('<p class="banner bad">%s</p>' % _text(error))

    cover = ui.pick_cover(base, jellyfin_link["jf_id"] if jellyfin_link else None,
                          row["poster_url"], row["art_source"] or "")
    if cover:
        parts.append('<img class="cover" src="%s" alt="" loading="lazy">'
                     % _text(cover))
    parts.append(_art_block(base, row, jellyfin_link))

    facts = _facts(row)
    if facts:
        parts.append('<p class="sub">%s</p>' % facts)
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
    parts.append('<form method="post" action="%stitle/%d/metadata">'
                 '<p><button type="submit">Refresh metadata</button></p>'
                 '</form>' % (_text(base), row["id"]))

    # the copies belong on the title page itself, not behind a tab
    copies_on = True
    tabs = ""
    parts.append(tabs)

    if True:   # the copies sit below; this branch is no longer exclusive
        parts.append(_copies_tab(base, row, items, digital, formats, labels,
                                 users, viewer_id))
    if True:   # play sheet and the about block belong on every view
        parts.append(_play_block(base, row, items, digital, players,
                                 last_player, jellyfin_link,
                                 jellyfin_configured, jellyfin_sessions,
                                 labels))
        parts.append('<details class="copy"><summary>Edit metadata</summary>')
        parts.append(_title_form(base, row))
        parts.append('</details>')

    if back is None:
        back = ("?kind=%s" % row["kind"]
                + ("&owner=%d" % owner if owner else ""))
    return ui.page(base, row["name"], "".join(parts), back=back)
