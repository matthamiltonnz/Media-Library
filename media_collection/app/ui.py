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
  .grid.players { max-height: 20rem; overflow-y: auto; }
  .grid.players label { display: flex; align-items: baseline; gap: .4rem;
                        opacity: 1; }
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
  .badges { display: flex; flex-wrap: wrap; gap: .4rem; margin: .2rem 0 .8rem; }
  .badge { border: 1px solid #8885; border-radius: 999px; padding: .15rem .6rem;
           font-size: .78rem; background: #8881; white-space: nowrap; }
  .tabs { display: flex; gap: .3rem; border-bottom: 1px solid #8884;
          margin: 1.2rem 0 .9rem; }
  .tabs a { padding: .4rem .8rem; border-radius: 6px 6px 0 0; font-size: .9rem;
            text-decoration: none; }
  .tabs a.on { background: #8882; font-weight: 600; }
  button.primary { font-size: 1rem; padding: .5rem 1.6rem; }
  .tally { text-decoration: none; border-bottom: 1px dotted currentColor; }
  .tally.current { font-weight: 700; border-bottom-style: solid; }
  img.cover { float: right; width: 170px; max-width: 38%; border-radius: 8px;
              margin: 0 0 .8rem 1.2rem; box-shadow: 0 6px 18px #0003; }
  button.danger { border-color: #b4453c; color: #e8837a; }
  dialog.sheet { border: 1px solid #8886; border-radius: 10px; padding: 1.2rem 1.4rem;
                 max-width: 34rem; width: 92%; background: Canvas; color: CanvasText; }
  dialog.sheet::backdrop { background: #0008; }
  dialog.sheet h2 { margin-top: 0; }
  dialog.sheet p.copy-play { display: flex; align-items: center; gap: .6rem;
                             flex-wrap: wrap; margin: .5rem 0; }
</style>
</head>
<body>
<p class="sub"><a href="@@BACK@@">← Back to the catalogue</a></p>
<h1>@@TITLE@@</h1>
@@CONTENT@@
</body>
</html>
"""


def page(base, title, content, back=""):
    return (SHELL.replace("@@BASE@@", html.escape(base))
                 .replace("@@BACK@@", html.escape(base + back))
                 .replace("@@TITLE@@", html.escape(title))
                 .replace("@@CONTENT@@", content))
