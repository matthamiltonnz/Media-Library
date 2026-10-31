"""Library admin: import, export, maintenance. Kept off the catalogue page."""
import html
import time

import ui


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
        state = ("Refreshing %d/%d — %d updated, %d missed, %d rate-limited (started %s)."
                 % (refresh["done"], refresh["total"], refresh["updated"],
                    refresh["failed"], refresh.get("throttled", 0), refresh["started"]))
        action = '<button type="submit" disabled>Running…</button>'
    elif refresh.get("paused"):
        state = ("Paused at %d/%d — Wikimedia rate-limited the requests (%d updated, "
                 "%d missed). Press the button again in a while; it resumes where it "
                 "stopped." % (refresh["done"], refresh["total"], refresh["updated"],
                               refresh["failed"]))
        action = '<button type="submit">Resume refresh</button>'
    elif refresh.get("finished"):
        state = ("Last run finished %s: %d updated, %d missed, %d rate-limited of %d."
                 % (refresh["finished"], refresh["updated"], refresh["failed"],
                    refresh.get("throttled", 0), refresh["total"]))
        action = '<button type="submit">Refresh all metadata</button>'
    else:
        state = "Never run."
        action = '<button type="submit">Refresh all metadata</button>'
    last = refresh.get("last") or ""
    return """
<h2>Metadata</h2>
<p>Walks every title in the background (one at a time — Wikipedia throttles bursts)
   and fills plot, poster, year and IMDb id. This page updates itself while it runs.</p>
<div id="refresh-panel" data-running="%s" data-status="%sadmin/status.json">
  <p id="refresh-state">%s</p>
  <p id="refresh-last" class="muted">%s</p>
  <form method="post" action="%sadmin/refresh">
    %s
    <label class="check"><input type="checkbox" name="force" value="1"> also overwrite
      titles that already have metadata</label>
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
            s.failed + ' missed, ' + s.throttled + ' rate-limited.';
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
       html.escape(base), action)


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
<h2>Media roots (NAS folders)</h2>
<p>The folders that ripped files live in. A playback source stores a root plus the
   path inside it, so moving the NAS only means editing the root — every row below
   can be changed in place and saved.</p>
%s
<form method="post" action="%sadmin/roots">
  <div class="grid">
    <label>Label<input name="label" placeholder="QNAP" required></label>
    <label>Path<input name="path" placeholder="/media/QNAP" required></label>
  </div>
  <p><button type="submit">Add root</button></p>
</form>
""" % (table, html.escape(base))


def render(base, viewer, stats, pending=0, message=None, refresh=None, duplicates=0,
           roots=(), jellyfin=None, scan_message="", players=(), chosen=None):
    parts = []
    if message:
        parts.append('<p class="banner good">%s</p>' % html.escape(message))

    parts.append(
        '<p class="sub">Signed in as <strong>%s</strong>. %d copies · %d titles · '
        "%d digital sources · %d owners.</p>"
        % (html.escape(viewer["display_name"]), stats["items"], stats["titles"],
           stats["digital"], stats["owners"]))

    parts.append("""
<h2>Import</h2>
<p>Load a Blu-ray.com / iTunes style CSV. Rows are staged for review first, so
   nothing lands in the catalogue until you commit them.</p>
<p><a href="%simport"><button type="button">Import a CSV…</button></a></p>
""" % html.escape(base))

    parts.append("""
<h2>Export</h2>
<p>A spreadsheet of every copy you can see, or a complete JSON backup.</p>
<p>
  <a href="%sexport.csv"><button type="button">Download CSV</button></a>
  <a href="%sexport.json"><button type="button">Download JSON backup</button></a>
</p>
""" % (html.escape(base), html.escape(base)))

    if pending:
        merge = ('<form method="post" action="%smerge"><p>%d item(s) from the bundled '
                 "catalogue are not in this library. "
                 '<button type="submit">Merge them</button></p></form>'
                 % (html.escape(base), pending))
    else:
        merge = ('<p class="muted">Nothing pending from the bundled catalogue - every '
                 "row it carries is already in this library. Duplicate titles are "
                 "listed below.</p>")
    dedupe = ('<form method="post" action="%sadmin/dedupe"><p>%d title(s) exist twice — '
              "typically one row from the disc import (no year) and one from iTunes. "
              '<button type="submit">Merge duplicate titles</button></p></form>'
              % (html.escape(base), duplicates)) if duplicates else \
             '<p class="muted">No duplicate titles.</p>'
    parts.append("<h2>Maintenance</h2>%s%s" % (merge, dedupe))

    parts.append(_refresh_block(base, refresh))
    parts.append(_roots_block(base, roots))
    parts.append(_scan_block(base, scan_message))
    if jellyfin is not None:
        parts.append(_jellyfin_block(base, jellyfin))
    if players:
        parts.append(_players_block(base, players, chosen))

    return ui.page(base, "Library admin", "".join(parts))


def _scan_block(base, message):
    """Re-walk the media folders so new rips pick up their titles by themselves."""
    return """
<h2>Media folders</h2>
%s
<form method="post" action="%sscan">
  <p><button type="submit">Scan the media folders</button>
     <span class="muted">Finds files that are already ripped but not yet linked to
     a title - films and shows by folder name and year, albums by artist.</span></p>
</form>""" % ("<p class=\"sub\">%s</p>" % html.escape(message) if message else
              '<p class="muted">Not scanned since the add-on started.</p>',
              html.escape(base))


def _players_block(base, players, chosen):
    """Which media players the Play box should offer.

    Home Assistant reports every media_player it can see - phones, TVs,
    receivers, cast targets - and most of them are not where anyone wants a
    film to start. Ticking the useful ones keeps that list short; ticking
    nothing means 'show them all' so it never blocks playback.
    """
    if not players:
        return ('<h2>Players</h2>\n'
                '<p class="sub">Home Assistant is not reporting any media'
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
    return """<h2>Players</h2>
<p class="sub">%s</p>
<form method="post" action="%sadmin/players">
  <div class="grid players">%s</div>
  <p><button type="submit">Save players</button>
     <span class="muted">Only ticked players appear on title pages.</span></p>
</form>""" % (note, html.escape(base), "".join(rows))



def _jellyfin_block(base, status):

    """Jellyfin: how much of the catalogue the server can already play."""
    kinds = status.get("kinds") or {}
    detail = ", ".join("%d %s" % (count, kind)
                       for kind, count in sorted(kinds.items())) or "nothing yet"
    if not status.get("configured"):
        state = ('<p class="sub">No API key yet - put <code>jellyfin_api_key</code> in the '
                 'add-on options (Settings, Add-ons, Media Collection, Configuration).</p>')
    else:
        last = (" Last sync %s." % html.escape(status["synced_at"])
                if status.get("synced_at") else " Not synced yet.")
        state = ('<p class="sub">%s - %d of %d copies linked (%s).%s</p>'
                 % (html.escape(status.get("url") or ""), status.get("linked") or 0,
                    status.get("copies") or 0, html.escape(detail), last))
    return """
<h2>Jellyfin</h2>%s
<form method="post" action="%sjellyfin/sync">
  <p><button type="submit">Sync with Jellyfin</button>
     <span class="muted">Links every copy to the item Jellyfin already holds: albums and
     TV seasons by folder, films by name and year.</span></p>
</form>
<details>
  <summary>Server and API key</summary>
  <form method="post" action="%sjellyfin/save">
    <div class="grid">
      <label>Server URL<input name="jellyfin_url" value="%s"
        placeholder="http://192.168.1.10:8096"></label>
      <label>API key<input name="jellyfin_api_key" type="password"
        placeholder="%s"></label>
    </div>
    <p><button type="submit">Save</button>
       <span class="muted">Jellyfin makes a key under Dashboard, API Keys. Leave the key
       empty to keep the one already stored.</span></p>
  </form>
</details>""" % (state, html.escape(base), html.escape(base),
                html.escape(status.get("url") or ""),
                "stored - type to replace" if status.get("configured") else "paste the key")
