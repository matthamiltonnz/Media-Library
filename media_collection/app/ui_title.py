"""Title detail page: the title itself, its copies, and one button to play it."""

import html

import ui

KIND_LABELS = {"movie": "Film", "tv": "TV", "music": "Music", "other": "Other"}

STORE_LABELS = {
    "itunes": "iTunes",
    "appletv": "Apple TV",
    "rip": "ripped file",
    "jellyfin": "Jellyfin",
    "other": "other store",
}
PURCHASE_STORES = ("itunes", "appletv", "other")


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


def _copy_title(item, labels, store=None):
    """'Blu-ray · Matt' - what a copy is, in one phrase."""
    bits = [labels.get(item["format"], item["format"] or "copy")]
    if store:
        bits.append(store)
    bits.append(item["owner_name"] or "unowned")
    return " · ".join(bits)


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


def _badges(items, digital, labels):
    """One badge per copy: format, store and owner, above the Play button."""
    stores = _stores(digital)
    badges = []
    for item in items:
        bits = [labels.get(item["format"], item["format"] or "copy")]
        if stores.get(item["id"]):
            bits.append(stores[item["id"]])
        bits.append(item["owner_name"] or "unowned")
        badges.append('<span class="badge">%s</span>' % _text(" · ".join(bits)))
    return "".join(badges)


def _play_block(base, row, items, digital, players, last_player,
                jellyfin_link, jellyfin_configured, sessions, labels):
    """The badges plus one Play button; the button opens every way to play."""
    playable = [source for source in digital
                if source["provider"] == "rip" and source["path"]
                and source["root_path"]]
    jellyfin_ready = bool(jellyfin_link and jellyfin_configured)
    badges = _badges(items, digital, labels)
    head = ('<div class="badges">%s</div>' % badges if badges
            else '<p class="muted">No copies recorded yet.</p>')
    if not playable and not jellyfin_ready:
        return (head + '<p class="muted">No file to play yet - rip the disc to'
                ' the NAS and the scan picks it up by itself.</p>')

    player_options = "".join(
        '<option value="%s"%s>%s%s</option>'
        % (_text(player["entity_id"]),
           " selected" if player["entity_id"] == last_player else "",
           _text(player["name"]),
           " (%s)" % _text(player["state"]) if player["state"] else "")
        for player in players)
    copies = "".join(
        '<p class="copy-play">'
        '<button type="submit" name="source_id" value="%d">Play %s</button>'
        ' <span class="muted">%s</span></p>'
        % (source["id"],
           _text("%s · %s" % (labels.get(source["copy_format"],
                                         source["copy_format"] or "copy"),
                              source["owner_name"] or "unowned")),
           _text(source["root_label"] or "media") + " · "
           + _text(source["path"]))
        for source in playable)
    if copies:
        copies = ('<form method="post" action="%stitle/%d/play">'
                  '<label>Player<select name="entity_id">%s</select></label>'
                  '%s</form>'
                  '<p class="muted">Home Assistant plays the file through its'
                  ' own media source, so the player only needs to understand'
                  ' the format.</p>'
                  % (_text(base), row["id"], player_options, copies))
    else:
        copies = '<p class="muted">No ripped file for this title yet.</p>'

    jellyfin = ""
    if jellyfin_ready:
        jellyfin = ('<form method="post" action="%stitle/%d/jellyfin">'
                    '<button type="submit">Play in Jellyfin</button>'
                    ' <span class="muted">Streams from the Jellyfin server.</span>'
                    '</form>' % (_text(base), row["id"]))
    playing = ""
    if sessions:
        playing = ('<p class="muted">Playing in Jellyfin now: %s</p>'
                   % _text(", ".join(str(session) for session in sessions)))

    return """%s
<p><button type="button" class="primary" data-open="play">Play</button></p>
<dialog id="play" class="sheet">
  <h2>Play %s</h2>%s
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
</script>""" % (head, _text(row["name"]), playing, jellyfin, copies)


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


def _copy_line(item, labels, store):
    """One line that says what the copy is and where it came from."""
    head = _copy_title(item, labels, store)
    bits = [value for value in (item["edition"], item["location"],
                                item["condition"]) if value]
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
    if not items:
        parts.append('<p class="muted">No copies yet.</p>')
    for item in items:
        store = stores.get(item["id"])
        if viewer_id is None or item["owner_id"] == viewer_id:
            remove = ('<form method="post" action="%sitems/%d/delete"'
                      " onsubmit=\"return confirm('Remove this copy?')\">"
                      '<p><button type="submit" class="danger">Delete copy'
                      '</button> <span class="muted">Drops the copy and its'
                      ' playback sources.</span></p></form>'
                      % (_text(base), item["id"]))
        else:
            remove = ('<p class="muted">Shared copy - only %s can delete'
                      ' it.</p>' % _text(item["owner_name"] or "its owner"))
        parts.append(
            '<details class="copy"><summary>%s</summary>'
            '<form method="post" action="%sitems/%d">%s'
            '<p><button type="submit">Save copy</button></p></form>%s'
            '</details>'
            % (_copy_line(item, labels, store), _text(base), item["id"],
               _copy_fields(item, formats, labels, store), remove))

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
        '<h2>Add a copy</h2>'
        '<form method="post" action="%sitems">'
        '<input type="hidden" name="title" value="%s">'
        '<input type="hidden" name="kind" value="%s">'
        '<input type="hidden" name="release_year" value="%s">'
        '<input type="hidden" name="from" value="title">'
        '<div class="grid"><label>Owner<select name="owner_id">%s</select>'
        '</label></div>%s'
        '<p><button type="submit">Add copy</button></p></form>'
        % (_text(base), _text(row["name"]), _text(row["kind"]),
           _text(row["release_year"] or ""), owner_options,
           _copy_fields(blank, formats, labels, "")))
    return "".join(parts)


def _title_form(base, row):
    kinds = (("movie", "Film"), ("tv", "TV"), ("music", "Music"),
             ("other", "Other"))
    options = "".join('<option value="%s"%s>%s</option>'
                      % (kind, " selected" if kind == row["kind"] else "",
                         _text(label))
                      for kind, label in kinds)
    return """
<h2>Title</h2>
<form method="post" action="%stitle/%d">
  <div class="grid">
    <label>Name<input name="name" value="%s"></label>
    <label>Year<input name="release_year" value="%s"></label>
    <label>Kind<select name="kind">%s</select></label>
    <label>IMDb ID<input name="imdb_id" value="%s"></label>
    <label>TMDb ID<input name="tmdb_id" value="%s"></label>
  </div>
  <p><button type="submit">Save title</button></p>
</form>""" % (_text(base), row["id"], _text(row["name"]),
             _text(row["release_year"] or ""), options, _text(row["imdb_id"]),
             _text(row["tmdb_id"] or ""))


def render(base, row, items, digital, formats, labels, summary=None, error=None,
           ripped_root="", roots=(), owner=None, players=(), last_player=None,
           jellyfin_link=None, jellyfin_sessions=(),
           jellyfin_configured=False, tab="about", users=(), viewer_id=None):
    parts = []
    if summary:
        parts.append('<p class="banner good">%s</p>' % _text(summary))
    if error:
        parts.append('<p class="banner bad">%s</p>' % _text(error))

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

    copies_on = tab == "copies"
    parts.append('<nav class="tabs">'
                 '<a href="%stitle/%d"%s>About</a>'
                 '<a href="%stitle/%d?tab=copies"%s>Copies (%d)</a></nav>'
                 % (_text(base), row["id"],
                    "" if copies_on else ' class="on"',
                    _text(base), row["id"],
                    ' class="on"' if copies_on else "", len(items)))

    if copies_on:
        parts.append(_copies_tab(base, row, items, digital, formats, labels,
                                 users, viewer_id))
    else:
        parts.append(_play_block(base, row, items, digital, players,
                                 last_player, jellyfin_link,
                                 jellyfin_configured, jellyfin_sessions,
                                 labels))
        parts.append(_title_form(base, row))

    back = "?kind=%s" % row["kind"] + ("&owner=%d" % owner if owner else "")
    return ui.page(base, row["name"], "".join(parts), back=back)
