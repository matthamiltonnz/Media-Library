"""Import pages: upload CSV, confirm column mapping, review, commit."""
import html

import importer

import ui

def upload_page(base, error=None):
    banner = '<p class="banner bad">%s</p>' % html.escape(error) if error else ""
    content = banner + """
<p class="sub">Blu-ray.com → My Movies → Export gives you a CSV. Upload it, check the
   column mapping on the next screen, then commit the rows you want.</p>
<form method="post" action="%simport" enctype="multipart/form-data">
  <p><input type="file" name="csv" accept=".csv,text/csv"></p>
  <p>…or paste the CSV:</p>
  <p><textarea name="paste" placeholder="Title,Year,Format,UPC,..."></textarea></p>
  <p><button type="submit">Read CSV</button></p>
</form>
""" % html.escape(base)
    return ui.page(base, "Import a CSV", content)


def mapping_form(base, imp, headers, mapping):
    rows = []
    for field, label in importer.FIELDS:
        current = mapping.get(field, "")
        opts = ['<option value="">— none —</option>']
        for header in headers:
            sel = " selected" if header == current else ""
            opts.append('<option value="%s"%s>%s</option>'
                        % (html.escape(header), sel, html.escape(header)))
        rows.append('<label>%s<select name="map_%s">%s</select></label>'
                    % (html.escape(label), field, "".join(opts)))
    return ('<form method="post" action="%simport/%d/map">'
            '<div class="grid">%s</div>'
            '<p><button type="submit">Apply mapping &amp; preview</button></p></form>'
            % (html.escape(base), imp["id"], "".join(rows)))


def preview_table(rows, limit=400):
    body = []
    for r in rows[:limit]:
        css = {"ok": "ok", "duplicate": "dup", "error": "err"}[r["status"]]
        body.append(
            "<tr><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            '<td class="%s">%s</td><td class="muted">%s</td></tr>'
            % (r["line_no"], html.escape(r["title"] or ""), r["release_year"] or "",
               html.escape(r["format"] or ""), html.escape(r["upc_ean"] or "—"),
               css, r["status"], html.escape(r["message"] or ""))
        )
    more = ""
    if len(rows) > limit:
        more = '<p class="muted">Showing first %d of %d rows.</p>' % (limit, len(rows))
    return ("<table><thead><tr><th>Line</th><th>Title</th><th>Year</th><th>Format</th>"
            "<th>Barcode</th><th>Status</th><th>Note</th></tr></thead>"
            "<tbody>%s</tbody></table>%s" % ("".join(body), more))


def mapping_page(base, imp, headers, mapping, rows=None, counts=None, summary=None):
    parts = []
    if summary:
        parts.append('<p class="banner %s">%s</p>'
                     % ("good" if summary.get("ok") else "bad", html.escape(summary["text"])))
    if imp["committed_at"]:
        parts.append('<p class="banner good">Committed %s.</p>'
                     % html.escape(imp["committed_at"]))
    parts.append(mapping_form(base, imp, headers, mapping))
    if counts:
        parts.append("<h2>Review — %d rows (%d ok, %d duplicate, %d error)</h2>"
                     % (sum(counts.values()), counts.get("ok", 0),
                        counts.get("duplicate", 0), counts.get("error", 0)))
    if rows:
        parts.append(preview_table(rows))
    if rows and not imp["committed_at"] and counts and counts.get("ok"):
        parts.append('<form method="post" action="%simport/%d/commit">'
                     '<p><button type="submit">Import %d rows</button></p></form>'
                     % (html.escape(base), imp["id"], counts["ok"]))
    elif rows and not imp["committed_at"]:
        parts.append('<p class="muted">Nothing importable — fix the mapping above.</p>')
    return ui.page(base, "Import %s" % (imp["filename"] or "CSV"), "".join(parts))
