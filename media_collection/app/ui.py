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
  /* poster grid + virtual cases, shared by every page that shows cards */
  .cards { display: grid; gap: .8rem; grid-template-columns: repeat(auto-fill, minmax(9.5rem, 1fr)); }
  .card { border: 1px solid #8883; border-radius: 8px; overflow: hidden; color: inherit;
          text-decoration: none; display: flex; flex-direction: column; }
  .card .thumb { aspect-ratio: 2 / 3; background: #8882; display: flex; align-items: center;
                 justify-content: center; font-size: .8rem; }
  .card .thumb .muted { opacity: .6; }
  .card .thumb img { width: 100%; height: 100%; object-fit: cover; }
  /* virtual cases: the artwork sits inside a drawn box, so a square cover never
     gets cropped into a poster and a Blu-ray looks like a Blu-ray */
  .thumb { position: relative; }
  .thumb.case-music { aspect-ratio: 1 / 1; }
  /* Blu-ray, 3D and 4K cases: coloured plastic with a printed band across the top,
     the way the real boxes look (blue / blue with 3D / black with 4K Ultra HD) */
  .thumb.case-blu, .thumb.case-3d, .thumb.case-4k, .thumb.case-dvd {
      aspect-ratio: 5 / 7; border-radius: 5px; overflow: hidden;
      display: grid; grid-template-columns: 12% 1fr; align-items: stretch;
      box-shadow: inset 0 0 0 1px #ffffff2e, 0 6px 14px #0006; }
  .thumb .spine {
      display: flex; align-items: center; justify-content: center;
      writing-mode: vertical-rl; transform: rotate(180deg);
      font-size: .55rem; font-weight: 700; letter-spacing: .06em; color: #fff;
      text-shadow: 0 1px 2px #0006;
      border-right: 1px solid #ffffff33;
      background: linear-gradient(90deg, #ffffff26, #ffffff0a); }
  .thumb.case-blu > .spine { font-style: italic; }
  .thumb.case-3d > .spine { letter-spacing: .1em; }
  .thumb.case-dvd > .spine { letter-spacing: .2em; }
  .thumb.case-blu { background: linear-gradient(180deg, #4fa6e3, #1e6cb8 32%, #0a3d7e); }
  .thumb.case-3d { background: linear-gradient(180deg, #4fa6e3, #1e6cb8 32%, #0a3566); }
  .thumb.case-4k { background: linear-gradient(180deg, #2a2a2a, #08080a 55%, #000); }
  .thumb.case-dvd { background: linear-gradient(180deg, #23252b, #0c0d10 60%, #050506); }
  .thumb.case-blu img, .thumb.case-3d img,
  .thumb.case-4k img, .thumb.case-dvd img {
      border-radius: 0; box-shadow: none; }
  /* a CD sits in a clear jewel case: hinge on the left, inlay under the plastic */
  .thumb.case-jewel { aspect-ratio: 1 / 1; padding: 0 0 0 12%; border-radius: 3px;
      overflow: hidden;
      background: linear-gradient(100deg, #d7e0e6 0 12%, #f2f7fa 12% 100%);
      box-shadow: inset 0 0 0 1px #ffffff, 0 6px 14px #0005; }
  .thumb.case-jewel::before { content: ""; position: absolute; left: 12%; top: 0;
      bottom: 0; width: 1px; background: #ffffffcc; }
  .thumb.case-jewel img { border: 0; box-shadow: none; }
  /* a vinyl sleeve is the artwork, edge to edge - no frame around it */
  .thumb.case-sleeve { aspect-ratio: 1 / 1; border-radius: 3px;
                       overflow: hidden; padding: 0; background: none; }
  .thumb.case-sleeve img { box-shadow: none; }
  .thumb.case-digital { background: #8881; }
  .card .cap { padding: .4rem .5rem; font-size: .82rem; display: grid; gap: .15rem; }
  .card .cap .muted { font-size: .74rem; }
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
