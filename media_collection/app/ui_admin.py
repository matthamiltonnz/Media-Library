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
    rows = "".join(
        "<tr><td>%s</td><td class=\"barcode\">%s</td><td>%s</td>"
        "<td><form method=\"post\" action=\"%sadmin/roots/%d/delete\">"
        "<button type=\"submit\">Remove</button></form></td></tr>"
        % (html.escape(root["label"]), html.escape(root["path"]),
           html.escape(root["medium"]), html.escape(base), root["id"])
        for root in roots)
    table = ("<table><thead><tr><th>Label</th><th>Path</th><th>Medium</th><th></th>"
             "</tr></thead><tbody>%s</tbody></table>" % rows) if roots else \
            '<p class="muted">No roots yet.</p>'
    return """
<h2>Media roots (NAS folders)</h2>
<p>The folders that ripped files live in. A playback source stores a root plus the
   path inside it, so moving the NAS only means editing the root.</p>
%s
<form method="post" action="%sadmin/roots">
  <div class="grid">
    <label>Label<input name="label" placeholder="Matt — Movies" required></label>
    <label>Path<input name="path" placeholder="/media/My Media/QNAP/Movies" required></label>
    <label>Medium<select name="medium">
      <option value="video">Video</option>
      <option value="music">Music</option>
      <option value="other">Other</option>
    </select></label>
  </div>
  <p><button type="submit">Add root</button></p>
</form>
""" % (table, html.escape(base))


def render(base, viewer, stats, pending=0, message=None, refresh=None, duplicates=0,
           roots=()):
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

    return ui.page(base, "Library admin", "".join(parts))
