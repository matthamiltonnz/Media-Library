"""Library admin: import, export, maintenance. Kept off the catalogue page."""
import html
import os
import time

import stores
import ui


def _panel(title, body, name="", folded=False):
    """One block of the admin page: a heading, then its controls.

    `name` gives the section an id, so a form in it can redirect back to
    `admin#name` and land where it was rather than at the top of the page.
    `folded` starts the block closed - a `<details>` whose summary is the
    heading - for a section that is set up once and then left alone.
    """
    ident = ' id="%s"' % html.escape(name, quote=True) if name else ""
    if folded:
        return ('<details class="panel fold"%s>\n<summary>%s</summary>\n%s'
                '</details>'
                % (ident, html.escape(title), body))
    return ('<section class="panel"%s>\n<h2>%s</h2>\n%s</section>'
            % (ident, html.escape(title), body))


def confirm_root(base, root, files, copies):
    """Ask before a media folder goes: what hangs off it goes too."""
    return ("""
<dialog class="sheet" open>
<h2>Remove %s?</h2>
<p>It accounts for %d file entr%s and %d cop%s. A copy that only this
   folder backs is removed as well.</p>
<form method="post" action="%sadmin/roots/%d/delete">
  <p><button type="submit" name="choice" value="files">Remove and forget its
     files</button>
     <button type="submit" name="choice" value="keep">Remove the source
     only</button>
     <a href="%sadmin">Cancel</a></p>
</form>
</dialog>""" % (html.escape(root["label"]),
                files, "y" if files == 1 else "ies",
                copies, "y" if copies == 1 else "ies",
                html.escape(base), root["id"], html.escape(base)))


def _clean_block(base, state):
    """Forget file records whose path has gone - an unplugged drive."""
    note = ""
    if state.get("running") or state.get("finished"):
        total = state.get("total") or 0
        routes = state.get("routes") or 0
        pruned = state.get("pruned") or 0
        files = ("no file-backed copies to check" if not total else
                 "%d of %d file%s checked"
                 % (state.get("checked") or 0, total,
                    "" if total == 1 else "s"))
        note = ('<p class="sub">Clean: %s, %d forgotten, %d %s%s</p>\n'
                % (files, state.get("gone") or 0,
                   pruned, "copy" if pruned == 1 else "copies",
                   "" if state.get("running") else " pruned - done"))
        verified = state.get("verified") or 0
        if verified or routes:
            note += ('<p class="sub">Jellyfin: %d %s checked against the '
                     "server%s, %d route%s dropped.</p>\n"
                     % (verified, "copy" if verified == 1 else "copies",
                        "" if len(state.get("skipped") or []) < 2 else "s",
                        routes, "" if routes == 1 else "s"))
        skipped = state.get("skipped") or []
        if skipped:
            note += ('<p class="sub">%s could not be asked which items it '
                     "still holds, so its copies were left alone.</p>\n"
                     % html.escape(", ".join(skipped)))
        if state.get("running") and state.get("phase"):
            note += ('<p class="sub">Clean: %s&hellip;</p>\n'
                     % html.escape(state["phase"]))
    return """\n%s<form method="post" action="%sadmin/clean"
      onsubmit="return confirm('Check every file and forget the ones that have gone?')">
  <p><button type="submit">Clean database</button>
     <span class="muted">checks every recorded file and forgets the ones
     that have gone, drops Jellyfin copies whose server has been removed,
     then prunes copies left with nothing. A copy still on a shelf, or still
     linked to a server, is never touched</span></p>
</form>""" % (note, html.escape(base))


def _maintenance_block(base, duplicates):
    """Fold titles that arrived twice: a disc import and a Jellyfin sync."""
    if not duplicates:
        return ('<p class="sub">No title has been imported twice, so there is '
                "nothing to fold together.</p>")
    return """
<p class="sub">%d title%s arrived twice - typically one row from the disc
   import and one from a Jellyfin sync. Folding keeps every copy.</p>
<form method="post" action="%sadmin/dedupe">
  <p><button type="submit">Merge duplicate titles</button></p>
</form>""" % (duplicates, "" if duplicates == 1 else "s", html.escape(base))


def _refresh_block(base, refresh):
    refresh = refresh or {}
    running = "1" if refresh.get("running") else "0"
    if refresh.get("running") and refresh.get("waiting"):
        state = ("Rate-limited at %d/%d — waiting until %s, then retrying by itself "
                 "(%d updated, %d missed)."
                 % (refresh["done"], refresh["total"],
                    time.strftime("%H:%M", time.localtime(refresh["next_try"])),
                    refresh["updated"], refresh["failed"]))
        action = '<button type="submit" disabled>Waiting…</button>'
    elif refresh.get("running"):
        state = ("Refreshing %d/%d — %d updated, %d filed, %d missed, "
                 "%d rate-limited (started %s)."
                 % (refresh["done"], refresh["total"], refresh["updated"],
                    refresh.get("filed", 0), refresh["failed"],
                    refresh.get("throttled", 0), refresh["started"]))
        action = '<button type="submit" disabled>Running…</button>'
    elif refresh.get("paused"):
        state = ("Paused at %d/%d — Wikimedia rate-limited the requests (%d updated, "
                 "%d missed). Press the button again in a while; it resumes where it "
                 "stopped." % (refresh["done"], refresh["total"], refresh["updated"],
                               refresh["failed"]))
        action = '<button type="submit">Resume refresh</button>'
    elif refresh.get("finished"):
        state = ("Last run finished %s: %d updated, %d filed, %d missed, "
                 "%d rate-limited of %d."
                 % (refresh["finished"], refresh["updated"],
                    refresh.get("filed", 0), refresh["failed"],
                    refresh.get("throttled", 0), refresh["total"]))
        action = '<button type="submit">Refresh metadata</button>'
    else:
        state = "Never run."
        action = '<button type="submit">Refresh metadata</button>'
    last = refresh.get("last") or ""
    hours = refresh.get("auto_hours") or 0
    nxt = refresh.get("auto_next") or 0
    if not hours:
        auto = ("The automatic pass is off - set refresh_hours to 24 for daily, "
                "168 for weekly, or leave 0 to look things up only when asked.")
    else:
        span = {24: "daily at 03:00", 168: "weekly"}.get(
            hours, "every %d hours" % hours)
        auto = "Automatic pass %s." % span
        if nxt:
            auto += " Next one %s." % time.strftime("%a %H:%M",
                                                    time.localtime(nxt))
    return """
<div id="refresh-panel" data-running="%s" data-status="%sadmin/status.json">
  <p id="refresh-state">%s</p>
  <p id="refresh-last" class="muted">%s</p>
  <p class="muted">%s</p>
  <form method="post" action="%sadmin/refresh">
    %s
    <p class="muted">One button, every lookup: films and TV from
      Wikipedia/Wikidata, albums from MusicBrainz, for every act its
      MusicBrainz id, country, start year and kind plus a biography, portrait
      and reading links from Wikipedia, and for every film the series it
      belongs to - which is what fills the collections. Only what is missing is
      asked for: a film that already has its metadata but no collection gets
      its series looked up, not a fresh copy of everything. The same thing runs
      by itself when the add-on starts, and again on the schedule above.</p>
    <label class="check"><input type="checkbox" name="force" value="1"> also
      overwrite titles that already have metadata</label>
  </form>
</div>
<script>
(function () {
  var panel = document.getElementById('refresh-panel');
  if (!panel || panel.dataset.running !== '1') { return; }
  var url = panel.dataset.status;
  function poll() {
    fetch(url, {headers: {'Accept': 'application/json'}, cache: 'no-store'})
      .then(function (r) { return r.json(); })
      .then(function (s) {
        if (!s.running) { window.location.reload(); return; }
        if (s.waiting) {
          var when = new Date(s.next_try * 1000);
          document.getElementById('refresh-state').textContent =
            'Rate-limited at ' + s.done + '/' + s.total + ' — waiting until ' +
            when.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) +
            ', then retrying by itself.';
        } else {
          document.getElementById('refresh-state').textContent =
            'Refreshing ' + s.done + '/' + s.total + ' — ' + s.updated + ' updated, ' +
            (s.filed || 0) + ' filed, ' + s.failed + ' missed, ' +
            s.throttled + ' rate-limited.';
        }
        document.getElementById('refresh-last').textContent = s.last || '';
        window.setTimeout(poll, 3000);
      })
      .catch(function () { window.setTimeout(poll, 5000); });
  }
  window.setTimeout(poll, 2000);
})();
</script>
""" % (running, html.escape(base), html.escape(state), html.escape(last),
       html.escape(auto), html.escape(base), action)


def _roots_block(base, roots):
    rows = []
    for root in roots:
        form_id = "root-%d" % root["id"]
        rows.append(
            "<tr>"
            '<td><input form="%s" name="label" value="%s" required></td>'
            '<td><input form="%s" name="path" class="barcode" value="%s"'
            ' required></td>'
            '<td><form id="%s" method="post" action="%sadmin/roots/%d">'
            '<button type="submit">Save</button></form>'
            '<form method="post" action="%sadmin/roots/%d/delete">'
            '<button type="submit">Remove</button></form></td></tr>'
            % (form_id, html.escape(root["label"], quote=True),
               form_id, html.escape(root["path"], quote=True),
               form_id, html.escape(base), root["id"],
               html.escape(base), root["id"]))
    rows = "".join(rows)
    table = ("<table><thead><tr><th>Label</th><th>Path</th><th></th>"
             "</tr></thead><tbody>%s</tbody></table>" % rows) if roots else \
            '<p class="muted">No roots yet.</p>'
    return """
<p class="sub">The folders that ripped files live in. A playback source stores a root plus the
   path inside it, so moving the drive only means editing the folder — every row below
   can be changed in place and saved.</p>
%s
<details class="add">
<summary>Add a root</summary>
<form method="post" action="%sadmin/roots">
  <div class="grid">
    <label>Label<input name="label" placeholder="QNAP" required></label>
    <label>Path<input name="path" placeholder="/media/QNAP" required></label>
  </div>
  <p><button type="submit">Add root</button></p>
</form>
</details>
""" % (table, html.escape(base))


DEVICE_KINDS = ("apple_tv", "kodi", "jellyfin", "bluray_player")


def _device_options(targets, chosen=""):
    out = ['<option value="">-</option>']
    for row in targets:
        out.append('<option value="%s"%s>%s</option>'
                   % (row["id"],
                      " selected" if str(row["id"]) == str(chosen or "") else "",
                      html.escape(row["name"])))
    return "".join(out)


SHOP_KINDS = (("digital", "digital shop"), ("retail", "retail shop"))
def _kind_options(chosen):
    return "".join('<option value="%s"%s>%s</option>'
                   % (value, " selected" if value == chosen else "", text)
                   for value, text in SHOP_KINDS)


def _player_options(rows):
    out = ['<option value="">-</option>']
    for row in rows:
        out.append('<option value="%s">%s</option>'
                   % (row["id"], html.escape(row["name"])))
    return "".join(out)


def _app_options(sources=None):
    """The datalist every app box offers.

    Two kinds of answer live in one field: an Android package (`play_media`)
    and a name a player lists (`select_source`). So it carries both - the
    packages we know, labelled, and every app name the attached players
    actually publish, which is the only trustworthy source for the second
    kind (the Apple TV app is "TV" in tvOS's source list, not "Apple TV").
    """
    seen = set()
    options = []
    for name, package in stores.APP_CHOICES:
        options.append('<option value="%s">%s &middot; Android package</option>'
                       % (html.escape(package, quote=True), html.escape(name)))
        seen.add(name.lower())
    listed = {}
    for entity, names in (sources or {}).items():
        for name in names:
            listed.setdefault(name, []).append(entity)
    for name in sorted(listed):
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        options.append('<option value="%s">%s &middot; listed by %s</option>'
                       % (html.escape(name, quote=True), html.escape(name),
                          html.escape(", ".join(listed[name][:2]))))
    return "".join(options[:160])


def _link_app(row, player, entities):
    """The app a (store, player) link opens, and a word if it cannot work.

    The link wins, then the store's own app, then the player's - the order
    `stores.playable_stores()` resolves them in, so this shows what pressing
    Play would actually send. A plain name only works on a player that lists
    it (`select_source`); a package goes through `play_media`, which an
    Android TV may refuse (see the androidtv_remote note below).
    """
    app = (player.get("app") or row.get("app_name")
           or player.get("app_name") or "").strip()
    entity = (player.get("ha_entity_id") or "").strip()
    info = (entities or {}).get(entity)
    if not app or not info:
        return app, ""
    listed = info.get("sources") or []
    picks = (info.get("domain") or "").lower() in ("select", "input_select")
    integration = (info.get("integration") or "").lower()
    if "." in app and " " not in app:
        if integration == "androidtv_remote":
            # Home Assistant sends `market://launch?id=<package>` for this,
            # and the bedroom TV answers "error" for every package (YouTube
            # too, per HA's own log) - so the click looks like it worked.
            return app, ("%s is driven by androidtv_remote, which can only"
                         " launch an app with market://launch; this set answers"
                         " that with an error (Home Assistant's log shows it,"
                         " this page cannot). Add the Android TV (ADB)"
                         " integration for the set and attach its player here."
                         % entity)
        return app, ""
    if app in listed:
        return app, ""
    if listed:
        return app, ("%s %s %s, not %s" % (entity,
                                          "offers" if picks else "lists",
                                          ", ".join(listed[:4]), app))
    if picks:
        return app, ("%s offers no options to pick - set one in Home"
                     " Assistant, or give this player a script instead"
                     % entity)
    return app, ("%s publishes no app list - give this player an Android"
                 " package instead" % entity)


def _store_dialog(base, row, opened, targets, entities=None):
    """The edit sheet for one store: its own fields, and its players."""

    entities = entities or {}
    attached = [player["id"] for player in opened]
    spare = [target for target in targets if target["id"] not in attached]
    options = "".join(
        '<option value="%s">%s &middot; %s</option>'
        % (target["id"], html.escape(target["name"]),
           html.escape(target["kind"])) for target in spare)
    players = []
    for player in opened:
        app, trouble = _link_app(row, player, entities)
        players.append(
            '<p>%s <span class="muted">%s &middot; %s</span></p>'
            '<div class="linkrow" style="display:flex;align-items:center;'
            'gap:.5rem;justify-content:space-between">'
            '<form method="post" action="%sstores/%d/link/%d">'
            '<input name="app" value="%s" list="app-choices" size="28"'
            ' placeholder="Apple TV or com.example.app">'
            ' <button type="submit">Save app</button></form>'
            '<form method="post" action="%sstores/%d/detach/%d">'
            '<button type="submit" class="danger">Detach</button></form>'
            "</div>"
            "%s"
            % (html.escape(player["name"]), html.escape(player["kind"]),
               html.escape(player["ha_entity_id"] or "-"),
               html.escape(base), row["id"], player["id"],
               html.escape(app, quote=True),
               html.escape(base), row["id"], player["id"],
               trouble and '<p><span class="warn">%s</span></p>'
               % html.escape(trouble) or ""))
    players = "".join(players)
    hint = stores.suggested_app(row["label"])
    if "kind" in row.keys() and row["kind"] == "format":
        # a format row is not a shop: its kind is fixed, so an edit cannot
        # quietly turn "Blu-ray" into a digital store
        kind_field = ('<label>Kind<input value="format" disabled>'
                      '<input type="hidden" name="kind" value="format">'
                      "</label>")
    else:
        kind_field = ('<label>Kind<select name="kind">%s</select></label>'
                      % _kind_options(row["kind"]))
    return (
        '<dialog class="sheet" id="store-%d">'
        "<h2>Edit %s</h2>"
        '<form method="post" action="%sstores/%d">'
        '<div class="grid">'
        '<label>Label<input name="label" value="%s"></label>'
        "%s"
        "</div>"
        '<p><button type="submit">Save</button></p>'
        "</form>"
        "<h3>Players</h3>"
        "%s"
        '<details class="add"><summary>Add a player</summary>'
        '<form method="post" action="%sstores/%d/attach">'
        '<p><select name="target_id">%s</select>'
        '<input name="app" list="app-choices" size="30" placeholder="%s">'
        ' <button type="submit">Add player</button></p>'
        ' <span class="muted">the app this store opens on that player. Pick a'
        " package from the list for an Android TV; a tvOS/Apple player wants"
        " the name it lists instead (Apple TV).</span>"
        "</form></details>"
        '<form method="dialog"><p><button type="submit">Close</button></p>'
        "</form>"
        "</dialog>"
        % (row["id"], html.escape(row["label"]),
           html.escape(base), row["id"],
           html.escape(row["label"], quote=True),
           kind_field,
           players or '<p class="muted">No player attached yet.</p>',
           html.escape(base), row["id"],
           options or '<option value="">-</option>',
           html.escape(hint or "Apple TV or com.example.app", quote=True)))


def _entity_options(entities, chosen=""):
    """Every entity a player can point at, grouped by domain.

    A `<select>`, not the `<input list>` this used to be: a datalist offers
    only what matches the text already in the box, so a field holding
    `input_select.lounge_activity` suggested exactly that one entity and the
    Harmony Hub's `select.lounge_activities` looked absent from the list.
    """
    chosen = (chosen or "").strip()
    rows = list(entities or ())
    groups = {}
    for entity in rows:
        groups.setdefault(entity.get("domain") or "other", []).append(entity)
    out = []
    if chosen and chosen not in {entity["entity_id"] for entity in rows}:
        # An entity nothing reports right now (its integration is off) has to
        # survive a save, so it rides along as the selected option.
        out.append('<option value="%s" selected>%s - not reported by Home'
                   ' Assistant</option>'
                   % (html.escape(chosen, quote=True), html.escape(chosen)))
    elif not rows:
        out.append('<option value="%s">%s</option>'
                   % (html.escape(chosen, quote=True),
                      html.escape(chosen or "-")))
    for domain in sorted(groups):
        out.append('<optgroup label="%s">' % html.escape(domain))
        for entity in groups[domain]:
            label = "%s \u00b7 %s" % (entity.get("name") or entity["entity_id"],
                                     entity["entity_id"])
            if entity.get("integration"):
                label += " \u00b7 %s" % entity["integration"]
            out.append('<option value="%s"%s>%s</option>'
                       % (html.escape(entity["entity_id"], quote=True),
                          " selected" if entity["entity_id"] == chosen else "",
                          html.escape(label)))
        out.append("</optgroup>")
    return "".join(out)


def _player_dialog(base, row, entities):
    """The edit sheet for one player: what it is called and what it drives.

    No Kind box: a player's kind *is* its entity's domain (`select.` picks an
    option, `media_player.` turns on and opens an app, `script.` runs), and the
    handler derives it from the posted entity. Asking for it again could only
    contradict the entity.
    """

    options = _entity_options(entities, row["ha_entity_id"])
    return (
        '<dialog class="sheet" id="player-%d">'
        "<h2>Edit %s</h2>"
        '<form method="post" action="%sstores/device/%d">'
        '<div class="grid">'
        '<label>Name<input name="name" value="%s"></label>'
        '<label>Entity<select name="ha_entity_id">%s</select></label>'
        '<label>Option (a select or input_select)<input name="input_source"'
        ' value="%s" placeholder="Blu-ray"></label>'
        "</div>"
        '<p><button type="submit">Save</button></p>'
        "</form>"
        '<form method="dialog"><p><button type="submit">Close</button></p>'
        "</form>"
        "</dialog>"
        % (row["id"], html.escape(row["name"]),
           html.escape(base), row["id"],
           html.escape(row["name"], quote=True),
           options,
           html.escape(row["input_source"] or "", quote=True)))


def _stores_block(base, store_rows, targets, entities=()):
    """The stores, and the players each one can be opened on.

    A store is a digital shop (Apple TV, Apple Music, Google TV, Bandcamp) or
    - kind 'retail' - a shop a disc was bought in; a player is whatever Home
    Assistant can wake: a media_player, an input_select that fires an
    automation, a remote, a script. One store takes any number of players and
    every link carries its own app, because one device serves several stores -
    the Apple TV app on the Apple TV, the Android package on the bedroom TV.

    Copies that came from Jellyfin or from a ripped file are not stores: they
    carry their own route and are edited with the copy, so the kind 'system'
    rows are left out of this panel.
    """
    shops = [row for row in store_rows if row["kind"] in ("digital", "retail")]
    formats = [row for row in store_rows if row["kind"] == "format"]
    sources = {entity["entity_id"]: entity.get("sources") or []
               for entity in entities or ()}
    info = {entity["entity_id"]: entity for entity in entities or ()}
    shops_of = {}
    for row in shops:
        for player in row.get("players") or []:
            shops_of.setdefault(player["id"], []).append(row["label"])

    def one_row(row, opened, removable, show_kind=True):
        names = ", ".join(html.escape(player["name"]) for player in opened)
        state = ("" if row["is_active"]
                 else ' <span class="muted">(retired)</span>')
        kind_cell = ("<td>%s</td>" % html.escape(row["kind"])
                     if show_kind else "")
        remove = ('<form method="post" action="%sstores/%d/delete">'
                  '<button type="submit" class="danger">Remove</button>'
                  "</form>" % (html.escape(base), row["id"])
                  if removable else "")
        return ('<tr><td>%s%s <span class="muted">%s</span></td>'
                "%s"
                "<td>%s</td>"
                '<td class="actions">'
                '<button type="button" data-open="store-%d">Edit</button> %s'
                "</td></tr>"
                % (html.escape(row["label"]), state, html.escape(row["key"]),
                   kind_cell,
                   names or '<span class="muted">-</span>',
                   row["id"], remove))

    rows = []
    dialogs = []
    for row in shops:
        opened = row.get("players") or []
        rows.append(one_row(row, opened, True))
        dialogs.append(_store_dialog(base, row, opened, targets, info))

    format_rows = []
    for row in formats:
        opened = row.get("players") or []
        format_rows.append(one_row(row, opened, False, show_kind=False))
        dialogs.append(_store_dialog(base, row, opened, targets, info))

    table = ('<table class="grid"><tr><th>Store</th><th>Kind</th>'
             "<th>Opens on</th><th></th></tr>%s</table>"
             % ("".join(rows) or
                '<tr><td colspan="4" class="muted">No store yet - press'
                " <em>Add a store</em> below.</td></tr>"))
    format_table = (
        "<h3>Players by format</h3>"
        '<p class="sub">A disc has no shop to open, but the room still has to'
        " be ready: attach the player, the amp input or a script to the"
        " <em>format</em> itself - Blu-ray, DVD, vinyl - and every title held"
        " on that format offers it in its Play sheet.</p>"
        '<table class="grid"><tr><th>Format</th><th>Opens on</th><th></th>'
        "</tr>%s</table>"
        % ("".join(format_rows) or
           '<tr><td colspan="3" class="muted">No format listed.</td></tr>'))



    add = (
        '<details class="add"><summary>Add a store</summary>'
        '<form method="post" action="%sstores">'
        '<div class="grid">'
        '<label>Label<input name="label" placeholder="Apple Music"></label>'
        '<label>Kind<select name="kind">%s</select></label>'
        "</div>"
        '<p><button type="submit">Add</button>'
        ' <span class="muted">digital for a store you stream or download'
        " from, retail for a shop you walked into.</span></p></form>"
        "</details>"
        % (html.escape(base), _kind_options("digital")))


    devices = []
    for row in targets:
        devices.append(
            '<tr><td>%s</td><td>%s</td><td>%s</td>'
            '<td class="actions">'
            '<button type="button" data-open="player-%d">Edit</button> '
            '<form method="post" action="%sstores/device/%d/delete">'
            '<button type="submit" class="danger">Remove</button>'
            "</form></td></tr>"
            % (html.escape(row["name"]),
               html.escape(row["ha_entity_id"] or ""),
               html.escape(", ".join(shops_of.get(row["id"]) or []))
               or '<span class="muted">-</span>',
               row["id"], html.escape(base), row["id"]))

    device_table = ('<table class="grid"><tr><th>Player</th>'
                    "<th>Entity</th><th>Opens</th><th></th></tr>%s</table>"
                    % "".join(devices) if devices else
                    '<p class="muted">No players yet - a player is what a'
                    " store opens on, so add one below.</p>")

    entity_options = ['<option value="">-</option>']
    for entity in entities or ():
        label = "%s \u00b7 %s" % (entity["name"], entity["entity_id"])
        if entity.get("kind"):
            label += " \u00b7 %s" % entity["kind"]
        if entity.get("integration"):
            label += " \u00b7 %s" % entity["integration"]
        if entity.get("sources"):
            label += " \u00b7 apps: %s" % ", ".join(entity["sources"][:4])
        entity_options.append('<option value="%s">%s</option>'
                              % (html.escape(entity["entity_id"], quote=True),
                                 html.escape(label)))

    add_device = (
        '<details class="add"><summary>Add a player</summary>'
        '<form method="post" action="%sstores/device">'
        '<div class="grid">'
        '<label>Entity<select name="ha_entity_id">%s</select></label>'
        '<label>Name<input name="name" placeholder="blank to use its own">'
        "</label>"
        '<label>Option (a select or input_select)<input name="input_source"'
        ' placeholder="Blu-ray"></label>'
        "</div>"
        '<p><button type="submit">Add</button>'
        ' <span class="muted">a media_player wakes and opens the app named on'
        " the store; a remote wakes the device; an input_select fires an"
        " automation; a script runs.</span></p></form></details>"
        % (html.escape(base), "".join(entity_options)))

    return ('<p class="sub">A store is a digital shop or platform a copy came'
            " from - Apple TV, Apple Music, Google TV, Bandcamp - or, kind"
            " <em>retail</em>, a shop a disc was bought in. Jellyfin and"
            " ripped-file copies are not stores: they carry their own route,"
            " so they are not listed here.</p>"
            + table + add + format_table
            + '<datalist id="app-choices">%s</datalist>' % _app_options(sources)
            + "".join(dialogs)
            + "<h3>Players</h3>"
            '<p class="sub">A player is what the app wakes. Attach one to a'
            " store in <em>Edit &lt;store&gt;</em> and give it the app that"
            " store opens; a store can run on several players, each with its"
            " own app.</p>"
            + device_table + "".join(_player_dialog(base, row, entities)
                                     for row in targets)
            + add_device)


def _build_stamp():
    """When the code behind this page was last written.

    There is no version number in the image (config.yaml stays with the
    Supervisor), and "I still see the old page" has cost several rounds: a
    stamp that changes on every deploy answers it in one look.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    newest = 0.0
    for name in os.listdir(here):
        if name.endswith(".py"):
            newest = max(newest, os.path.getmtime(os.path.join(here, name)))
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(newest)) if newest else ""


def render(base, viewer, stats, message=None, refresh=None, duplicates=0,
           roots=(), jellyfin=None, scan_message="", players=(), chosen=None,
           jobs=None, store_rows=(), targets=(), entities=(), confirm="",
           open_players=False):
    jobs = jobs or {}
    parts = []
    if confirm:
        parts.append(confirm)
    if message:
        parts.append('<p class="banner good">%s</p>' % html.escape(message))

    parts.append(
        '<p class="sub">Signed in as <strong>%s</strong>. '
        "%d titles &middot; %d copies &middot; %d digital sources"
        " &middot; %d owners.</p>"
        % (html.escape(viewer["display_name"]), stats["titles"],
           stats["items"], stats["digital"], stats["owners"]))

    if jellyfin is not None:
        sync = jobs.get("jellyfin") or {}
        progress = ""
        if sync.get("running"):
            progress = ('<p class="sub">Syncing: %s &mdash; %d of %d copies.</p>'
                        % (html.escape(sync.get("phase")
                                       or "reading the library"),
                           sync.get("done") or 0, sync.get("total") or 0))
        parts.append(_panel("Jellyfin",
                            progress + _jellyfin_block(base, jellyfin),
                        name="jellyfin"))

    parts.append(_panel("Media folders",
                        _roots_block(base, roots)
                        + _scan_block(base, scan_message, len(roots)),
                        name="roots"))

    metadata = ('<p class="sub">Films and TV get plot, poster, year '
                "and IMDb id from Wikipedia and Wikidata; albums get "
                "a MusicBrainz release, year and cover art. "
                "MusicBrainz has no plot to give, so an album never "
                "gains one. Both jobs run in the background and this "
                'page keeps itself up to date.</p>\n'
                + _refresh_block(base, refresh))
    if (jobs.get("artists") or {}).get("running"):
        # numbers only mean something while the fill is actually running
        metadata += _artists_block(base)
    parts.append(_panel("Metadata", metadata, name="metadata"))

    parts.append(_panel("Stores and platforms",
                        _stores_block(base, store_rows, targets, entities),
                        name="stores"))

    if players:
        parts.append(_panel("Home Assistant Players",
                            _players_block(base, players, chosen),
                            name="players", folded=not open_players))

    parts.append(_panel("Import and export", """
<p class="sub">A Blu-ray.com or iTunes style CSV is staged for review first,
   so nothing lands in the catalogue until you commit it. Export gives a
   spreadsheet of every copy you can see, or a full JSON dump.</p>
<p><a href="%simport"><button type="button">Import a CSV&hellip;</button></a>
   <a href="%sexport.csv"><button type="button">Download CSV</button></a>
   <a href="%sexport.json"><button type="button">Download JSON</button></a></p>
""" % (html.escape(base), html.escape(base), html.escape(base))))

    parts.append(_panel("Maintenance",
                        _maintenance_block(base, duplicates)
                        + _clean_block(base, jobs.get("clean") or {}),
                        name="maintenance"))

    if any((job or {}).get("running") for job in jobs.values()):
        parts.append('<p class="sub" id="progress">Working&hellip;</p>\n'
                     "<script>setTimeout(function () { location.reload(); },"
                     " 4000);</script>")

    stamp = _build_stamp()
    if stamp:
        parts.append('<p class="sub">Built from code edited %s. A page showing'
                     " an older time is a page that was not reloaded.</p>"
                     % html.escape(stamp))

    return ui.page(base, "Library admin", "".join(parts))


def _scan_block(base, message, roots=0):
    """Re-walk the media folders so new rips pick up their titles by themselves."""
    if not roots:
        # nothing to walk: offering the button only produces an error
        note = ('<p class="sub">%s</p>' % html.escape(message)
                if message else "")
        return ('%s'
                '<p class="muted">No media folders are set up yet - press'
                " <em>Add a root</em> above so the scan has somewhere"
                " to look.</p>"
                % note)
    return """
%s
<form method="post" action="%sscan">
  <p><button type="submit">Scan the media folders</button>
     <span class="muted">Finds files that are already ripped but not yet linked to
     a title - films and shows by folder name and year, albums by artist.</span></p>
</form>""" % ("<p class=\"sub\">%s</p>" % html.escape(message) if message else
              '<p class="muted">Not scanned since the add-on started.</p>',
              html.escape(base))


def _artists_block(base):
    """Fill the artists table from MusicBrainz - no Jellyfin needed."""
    import artists

    state = artists.summary()
    return """<h3 id="artists">Artists</h3>
<p class="sub">%s</p>
""" % (state or "Artist details come from MusicBrainz: the id, the country"
               " and the year the act started.", )


def _players_block(base, players, chosen):
    """Which media players the Play box should offer.

    Home Assistant reports every media_player it can see - phones, TVs,
    receivers, cast targets - and most of them are not where anyone wants a
    film to start. Ticking the useful ones keeps that list short; ticking
    nothing means 'show them all' so it never blocks playback.
    """
    if not players:
        return ('<p class="sub">Home Assistant is not reporting any media'
                ' players that can take a file yet.</p>')
    rows = []
    for player in players:
        short = player["entity_id"].replace("media_player.", "")
        rows.append(
            '<label><input type="checkbox" name="player" value="%s"%s> %s'
            '<span class="muted"> (%s · %s)</span></label>'
            % (html.escape(player["entity_id"]),
               " checked" if chosen and player["entity_id"] in chosen else "",
               html.escape(player["name"]), html.escape(player["state"]),
               html.escape(short)))
    if chosen:
        ticked = [player for player in players
                  if player["entity_id"] in set(chosen)]
        if ticked:
            note = ("The Play box offers these %d of the %d Home Assistant reports."
                    " Untick everything to go back to offering them all."
                    % (len(ticked), len(players)))
        else:
            note = ("None of the saved players is available right now - title pages"
                    " will offer them again as soon as they are back.")
    else:
        note = ("All %d players Home Assistant reports are offered on title pages."
                " Tick the ones you use to shorten that list." % len(players))
    return """<p class="sub">%s</p>
<form method="post" action="%sadmin/players">
  <div class="grid players">%s</div>
  <p><button type="submit">Save players</button>
     <button type="submit" name="clear" value="1">Offer every player</button>
     <span class="muted">Only ticked players appear on title pages.</span></p>
</form>""" % (note, html.escape(base), "".join(rows))



def _jellyfin_block(base, status):
    """Jellyfin servers: what is registered, what it holds, and a sync."""
    servers = status.get("servers") or []
    kinds = status.get("kinds") or {}
    detail = ", ".join("%d %s" % (count, kind)
                       for kind, count in sorted(kinds.items())) or "nothing"
    last = ("Last sync %s." % html.escape(str(status["synced_at"]))
            if status.get("synced_at") else "Never synced.")
    parts = []
    if servers:
        rows = []
        for server in servers:
            key = ('<span class="ok">set</span>' if server.get("api_key")
                   else '<span class="dup">missing</span>')
            name = html.escape(server["label"])
            label = html.escape(server["label"], quote=True)
            url = html.escape(server["url"], quote=True)
            paused = ("" if server.get("is_active")
                      else ' <span class="muted">(paused)</span>')
            rows.append(
                "<tr><td>%s%s</td>"
                '<td><a href="%s" target="_blank" rel="noreferrer">%s</a></td>'
                "<td>%s</td>"
                '<td><form method="post" action="%sjellyfin/server/%d/delete"'
                " onsubmit=\"return confirm('Remove this server? The copies"
                " it alone backs go too.')\">"
                '<button type="submit" class="danger">Remove</button>'
                "</form></td></tr>"
                % (name, paused, url, url, key, html.escape(base),
                   server["id"]))
            # a rotated key or a moved address is edited in place, and the
            # key itself is never sent back to the browser
            rows.append(
                '<tr class="edit"><td colspan="4"><details>'
                "<summary>Edit %s</summary>"
                '<form method="post" action="%sjellyfin/server/%d">'
                '<div class="grid">'
                '<label>Label<input name="label" value="%s" required></label>'
                '<label>Address<input name="url" value="%s" required></label>'
                '<label>API key<input name="api_key" type="password"'
                ' placeholder="leave blank to keep the current key"></label>'
                "</div>"
                '<p><button type="submit">Save</button>'
                ' <span class="muted">the key is never shown - type a new one'
                " to replace it, then run a sync.</span></p>"
                "</form></details></td></tr>"
                % (name, html.escape(base), server["id"], label, url))
        parts.append("<table><thead><tr><th>Server</th><th>Address</th>"
                     "<th>API key</th><th></th></tr></thead>"
                     "<tbody>%s</tbody></table>" % "".join(rows))
        routes = status.get("routes")
        if routes is None:                 # a status dict from before 0.23.74
            routes = status.get("linked") or 0
        line = ("%d cop%s can play from Jellyfin"
                % (routes, "y" if routes == 1 else "ies"))
        if kinds:
            line += " (%s matches)" % html.escape(detail)
        parts.append('<p class="sub">%s &middot; %s</p>' % (line, last))
        if any(server.get("is_active") for server in servers):
            parts.append("""
<form method="post" action="%sjellyfin/sync">
  <p><button type="submit">Sync with Jellyfin</button>
     <span class="muted">links every copy to the item Jellyfin holds - films
     by name and year, TV seasons and albums by folder.</span></p>
</form>""" % html.escape(base))
    else:
        parts.append('<p class="sub">No server yet. Add one below and its '
                     "films, shows and albums can be linked to the "
                     "catalogue.</p>")
    parts.append("""
<details class="add">
<summary>Add a server</summary>
<form method="post" action="%sjellyfin/server">
  <div class="grid">
    <label>Label<input name="label" placeholder="Goodwood"></label>
    <label>Address<input name="url" placeholder="http://192.168.1.10:8096"
      required></label>
    <label>API key<input name="api_key"
      placeholder="Dashboard - API keys"></label>
  </div>
  <p><button type="submit">Add server</button>
     <span class="muted">each server keeps its own routes, so two libraries
     can both be linked.</span></p>
</form>
</details>""" % html.escape(base))
    return "".join(parts)
