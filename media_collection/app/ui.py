"""Shared page shell for every screen (same look, same back-link)."""
import html

SHELL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<base href="@@BASE@@">
<title>@@TITLE@@</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 15px/1.45 -apple-system, "Segoe UI", Roboto, sans-serif;
         margin: 0; padding: 1.2rem; background: Canvas; color: CanvasText; }
  h1 { font-size: 1.3rem; margin: 0 0 .2rem; }
  h2 { font-size: 1rem; margin: 1.6rem 0 .6rem; }
  .sub { opacity: .7; margin: 0 0 1rem; }
  table { border-collapse: collapse; width: 100%; }
  th, td { text-align: left; padding: .3rem .45rem; border-bottom: 1px solid #8883;
           vertical-align: top; }
  th { font-size: .75rem; text-transform: uppercase; letter-spacing: .03em; opacity: .65; }
  input, select, textarea, button { font: inherit; padding: .4rem .5rem; border-radius: 6px;
      border: 1px solid #8886; background: Canvas; color: CanvasText; }
  button { cursor: pointer; font-weight: 600; }
  textarea { width: 100%; min-height: 7rem; font-family: ui-monospace, Menlo, monospace; }
  .grid { display: grid; gap: .5rem .9rem;
          grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr)); max-width: 60rem; }
  .grid label { display: grid; gap: .2rem; font-size: .8rem; opacity: .85; }
  .banner { padding: .5rem .7rem; border-radius: 6px; margin-bottom: 1rem; }
  .bad { background: #c33; color: #fff; }
  .good { background: #2a7; color: #fff; }
  .ok { color: #2a7; } .dup { color: #c80; } .err { color: #c33; }
  .muted { opacity: .6; }
  .tag { font-size: .72rem; border: 1px solid #8886; border-radius: 999px;
         padding: 0 .45rem; white-space: nowrap; }
  .barcode { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .82rem; }
  .poster { float: right; width: 12rem; margin: 0 0 1rem 1rem; border-radius: 8px; }
  details.copy { border: 1px solid #8883; border-radius: 8px; padding: .45rem .65rem;
                 margin-bottom: .5rem; }
  details.copy summary { cursor: pointer; font-size: .9rem; }
  details.copy[open] summary { margin-bottom: .6rem; }
  form.inline { display: inline; }
</style>
</head>
<body>
<p class="sub"><a href="@@BASE@@">← Back to the catalogue</a></p>
<h1>@@TITLE@@</h1>
@@CONTENT@@
</body>
</html>
"""


def page(base, title, content):
    return (SHELL.replace("@@BASE@@", html.escape(base))
                 .replace("@@TITLE@@", html.escape(title))
                 .replace("@@CONTENT@@", content))
