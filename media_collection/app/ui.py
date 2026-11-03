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
  /* the cover and the pencil that changes it share one box */
  .coverwrap { float: right; clear: right; position: relative;
               width: 170px; max-width: 38%; margin: 0 0 .8rem 1.2rem; }
  /* the image fills the box: a percentage max-width *inside* a float whose own
     width depends on the image collapses the picture to a thumbnail */
  .coverwrap img.cover { float: none; margin: 0; display: block;
                         width: 100%; max-width: none; height: auto; }
  .coverwrap .cover.none { display: block; width: 170px; height: 245px;
                           border-radius: 8px; background: #8882; }
  .coveredit { position: absolute; right: .45rem; bottom: .45rem; width: 2rem;
               height: 2rem; padding: 0; border-radius: 50%; cursor: pointer;
               font-size: 1rem; line-height: 1; border: 1px solid #8888;
               background: #fff9; color: #222; opacity: .85; }
  .coveredit:hover, .coveredit:focus-visible { opacity: 1; background: #fff; }
  @media (prefers-color-scheme: dark) {
    .coveredit { background: #000b; color: #eee; }
  }
  .artpick { position: relative; }
  .artpick .forget { position: absolute; top: -.45rem; right: -.45rem;
                     width: 1.3rem; height: 1.3rem; padding: 0;
                     border-radius: 50%; font-size: .8rem; line-height: 1;
                     opacity: .85; }
  .artpick .forget:hover { opacity: 1; background: #c33; color: #fff; }
  .artpick .auto { font-weight: 600; }
  /* an act's biography, above their albums */
  .bio { display: flex; gap: 1rem; align-items: flex-start;
         margin: .7rem 0 .2rem; max-width: 62rem; }
  .bio .portrait { width: 9rem; flex: 0 0 auto; border-radius: 8px;
                   box-shadow: 0 6px 18px #0003; }
  .bio div { min-width: 0; }
  @media (max-width: 34rem) {
    .bio { flex-direction: column; }
    .bio .portrait { width: 6rem; }
  }
  .artpicks { display: flex; flex-wrap: wrap; gap: .5rem; margin: 0 0 1rem; }
  .artpick { margin: 0; display: grid; gap: .2rem; justify-items: center; }
  .artpick button { padding: 0; border: 1px solid #8886; border-radius: 6px;
                    overflow: hidden; background: none; line-height: 0; }
  .artpick img { width: 74px; height: 111px; object-fit: cover; display: block; }
  .artpick span { font-size: .7rem; opacity: .7; }
  .artpick.current button { outline: 2px solid #4a9c6d; outline-offset: 1px; }
  button.danger { border-color: #b4453c; color: #e8837a; }
  dialog.sheet { border: 1px solid #8886; border-radius: 10px; padding: 1.2rem 1.4rem;
                 max-width: 34rem; width: 92%; background: Canvas; color: CanvasText; }
  dialog.sheet::backdrop { background: #0008; }
  dialog.sheet h2 { margin-top: 0; }
  dialog.sheet p.copy-play { display: flex; align-items: center; gap: .6rem;
                             flex-wrap: wrap; margin: .5rem 0; }
  /* poster grid + virtual cases, shared by every page that shows cards */
  /* filter pills: the kind row on the catalogue, the owner row everywhere */
  nav.modes { display: flex; gap: .4rem; margin: 0 0 .7rem; flex-wrap: wrap; }
  nav.modes a { padding: .38rem .85rem; border-radius: 999px;
                border: 1px solid #8886; color: inherit;
                text-decoration: none; font-weight: 600; font-size: .9rem; }
  nav.modes a.current { background: #2f6f4f; border-color: #2f6f4f; color: #fff; }
  nav.modes .n { opacity: .7; font-weight: 400; margin-left: .3rem;
                 font-size: .82rem; }
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
      aspect-ratio: auto; border-radius: 5px; overflow: hidden;
      display: grid; grid-template-rows: auto auto; align-items: stretch;
      box-shadow: inset 0 0 0 1px #ffffff2e, 0 6px 14px #0006; }
  .thumb .band {
      display: flex; align-items: center; justify-content: center;
      padding: .2rem .25rem; white-space: nowrap;
      font-size: .55rem; font-weight: 700; letter-spacing: .06em; color: #fff;
      text-shadow: 0 1px 2px #0006;
      border-bottom: 1px solid #ffffff33;
      background: linear-gradient(180deg, #ffffff26, #ffffff0a); }
  .thumb.case-blu > .band { font-style: italic; }
  .thumb.case-3d > .band { letter-spacing: .1em; }
  .thumb.case-dvd > .band { letter-spacing: .2em; }
  .thumb.case-blu { background: linear-gradient(180deg, #4fa6e3, #1e6cb8 32%, #0a3d7e); }
  .thumb.case-3d { background: linear-gradient(180deg, #4fa6e3, #1e6cb8 32%, #0a3566); }
  .thumb.case-4k { background: linear-gradient(180deg, #2a2a2a, #08080a 55%, #000); }
  .thumb.case-dvd { background: linear-gradient(180deg, #23252b, #0c0d10 60%, #050506); }
  .thumb.case-blu img, .thumb.case-3d img,
  .thumb.case-4k img, .thumb.case-dvd img {
      /* the band sits on top, so the cover keeps its full 2:3 and nothing is cropped */
      width: 100%; height: auto; aspect-ratio: 2 / 3; object-fit: cover;
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
  .thumb.case-digital { aspect-ratio: auto; border-radius: 5px; overflow: hidden;
                        display: grid; grid-template-rows: auto auto;
                        background: #8881; }
  .thumb.case-digital img { width: 100%; height: auto; aspect-ratio: 2 / 3;
                            object-fit: cover; }
  .thumb .band.plain { background: #fff; color: #111; border-bottom-color: #ccc; }
  .card .cap { padding: .4rem .5rem; font-size: .82rem; display: grid; gap: .15rem; }
  .card .cap .muted { font-size: .74rem; }
  .card .cap .playmark { display: block; margin-top: .2rem; font-size: .78rem; }
  /* the admin page: one bordered block per job, so each reads as a unit */
  .panel { border: 1px solid #8883; border-radius: 9px;
           padding: .1rem 1rem 1rem; margin: 0 0 1rem;
           max-width: 62rem; background: #8881; }
  .panel > h2 { margin-top: .9rem; }
  .panel > h3 { font-size: .92rem; margin: 1.2rem 0 .4rem; }
  .panel table { margin: .2rem 0 .6rem; }
  .panel tbody td { border-top: 1px solid #8882; }
  .panel form { margin: .4rem 0 0; }
  .panel .grid { margin-top: .2rem; }
  .panel details summary { cursor: pointer; font-size: .85rem; opacity: .85; }
  .panel tr.edit td { border-top: 0; padding-top: 0; }
  /* "Add a server" / "Add a root": a button that opens the form under it */
  .panel details.add { margin: .6rem 0 0; }
  .panel details.add > summary { display: inline-block; padding: .25rem .6rem;
      border: 1px solid #8886; border-radius: 5px; background: Canvas;
      font-size: .85rem; font-weight: 600; opacity: 1; list-style: none; }
  .panel details.add > summary::-webkit-details-marker { display: none; }
  .panel details.add > summary::before { content: "+ "; }
  .panel details.add[open] > summary::before { content: "\2212 "; }
</style>
</head>
<body>
<p class="sub" style="display:flex;justify-content:space-between;gap:1rem"><a href="@@BACK@@">@@BACKLABEL@@</a><a href="@@ADMIN@@">admin</a></p>
<h1>@@TITLE@@</h1>
@@CONTENT@@
@@NOWPLAYING@@
<script>
(function () {
  document.addEventListener('click', function (event) {
    var opener = event.target.closest('[data-open]');
    if (!opener) return;
    var sheet = document.getElementById(opener.dataset.open);
    if (sheet && !sheet.open) sheet.showModal();
  });
  var dlg = document.getElementById('play');
  if (!dlg) return;
  dlg.addEventListener('toggle', function (e) {
    if (!e.target.open) return;
    var file = document.getElementById('playfile');
    if (!file) return;
    var row = file.closest('p');
    if (row && dlg.firstElementChild !== row) dlg.insertBefore(row, dlg.firstElementChild);
  });
})();
</script>
</body>
</html>
"""


# The pinned player. Every page carries it - both the shell built here
# and the catalogue's own PAGE in server.py - because "Play in Jellyfin"
# used to leave the shelf with no idea what it had just started.
NOW_PLAYING = """<style>
  /* the pinned player: what Jellyfin is playing, on every page */
  body.has-nowplaying { padding-bottom: 5.2rem; }
  #nowplaying { position: fixed; left: .6rem; right: .6rem; bottom: .6rem;
                z-index: 20; display: flex; align-items: center; gap: .7rem;
                margin: 0 auto; max-width: 46rem; padding: .45rem .7rem;
                border-radius: .7rem; border: 1px solid #8886;
                background: Canvas; color: CanvasText;
                box-shadow: 0 8px 26px #0004; }
  #nowplaying[hidden] { display: none; }
  #nowplaying img { width: 2.6rem; height: 2.6rem; object-fit: cover;
                    border-radius: .4rem; background: #8882; flex: 0 0 auto; }
  #nowplaying .np-text { min-width: 0; flex: 1 1 auto; }
  #nowplaying .np-name { display: block; font-size: .9rem;
                         text-decoration: none; color: inherit;
                         white-space: nowrap; overflow: hidden;
                         text-overflow: ellipsis; }
  #nowplaying .np-sub { display: block; font-size: .75rem; opacity: .7;
                        white-space: nowrap; overflow: hidden;
                        text-overflow: ellipsis; }
  /* the player switcher: shown only when a second player is going */
  #nowplaying .np-players { display: flex; gap: .3rem; margin-top: .3rem;
                            overflow: hidden; }
  #nowplaying .np-players[hidden] { display: none; }
  #nowplaying .np-players button { max-width: 9rem; padding: .05rem .45rem;
                                   font-size: .68rem; border-radius: 999px;
                                   border: 1px solid #8886; background: none;
                                   color: inherit; cursor: pointer;
                                   white-space: nowrap; overflow: hidden;
                                   text-overflow: ellipsis; }
  #nowplaying .np-players button.current { background: #2f6f4f;
                                           border-color: #2f6f4f;
                                           color: #fff; }
  #nowplaying .np-players button.paused { opacity: .6; }
  #nowplaying .np-bar { display: block; height: .2rem; margin-top: .3rem;
                        border-radius: .1rem; background: #8883; }
  #nowplaying .np-bar span { display: block; height: 100%; width: 0;
                             border-radius: .1rem; background: currentColor; }
  /* the transport buttons: play/pause and stop, same size, same border */
  #nowplaying .npbtn { display: grid; place-items: center; flex: 0 0 auto;
                       width: 2.3rem; height: 2.3rem; padding: 0;
                       border-radius: 50%; border: 1px solid #8886;
                       background: transparent; color: inherit; cursor: pointer; }
  #nowplaying .npbtn:hover { background: #8882; }
  #nowplaying .npbtn:focus-visible { outline: 2px solid currentColor;
                                     outline-offset: 2px; }
  #nowplaying .npbtn svg { width: 1.05rem; height: 1.05rem; fill: currentColor; }
  /* previous/next belong to music, rewind/fast-forward to everything */
  #nowplaying .npbtn.prev, #nowplaying .npbtn.next { display: none; }
  #nowplaying.music .npbtn.prev,
  #nowplaying.music .npbtn.next { display: grid; }
  /* six buttons on a phone: tighter, and the cover makes way */
  @media (max-width: 27rem) {
    #nowplaying { gap: .45rem; }
    #nowplaying img { display: none; }
    #nowplaying .npbtn { width: 2rem; height: 2rem; }
  }
  /* one toggle, two faces: the glyph shows what pressing it will do */
  #nowplaying .i-play { display: none; }
  #nowplaying.paused .i-play { display: block; }
  #nowplaying.paused .i-pause { display: none; }
</style>
<aside id="nowplaying" hidden>
<img id="np-cover" alt="">
<span class="np-text"><a id="np-name" class="np-name" href="#"></a><span
 id="np-sub" class="np-sub"></span><span id="np-players" class="np-players" hidden></span><span class="np-bar"><span
 id="np-fill"></span></span></span>
<button type="button" class="npbtn prev" id="np-prev" title="Previous track"
 aria-label="Previous track"><svg viewBox="0 0 24 24" aria-hidden="true"><path
 d="M6 5h2.2v14H6zM20 5.5v13L9.6 12z"/></svg></button>
<button type="button" class="npbtn" id="np-rew" title="Rewind"
 aria-label="Rewind"><svg viewBox="0 0 24 24" aria-hidden="true"><path
 d="M11 5.5v13L2 12zM20 5.5v13L11 12z"/></svg></button>
<button type="button" class="npbtn" id="np-toggle" title="Pause"
 aria-label="Pause"><svg class="i-pause" viewBox="0 0 24 24" aria-hidden="true"
 ><rect x="7" y="5" width="4" height="14" rx="1.2"/><rect x="13" y="5" width="4"
 height="14" rx="1.2"/></svg><svg class="i-play" viewBox="0 0 24 24"
 aria-hidden="true"><path d="M8 5.2v13.6L19 12z"/></svg></button>
<button type="button" class="npbtn" id="np-fwd" title="Fast forward"
 aria-label="Fast forward"><svg viewBox="0 0 24 24" aria-hidden="true"><path
 d="M13 5.5v13L22 12zM4 5.5v13L13 12z"/></svg></button>
<button type="button" class="npbtn next" id="np-next" title="Next track"
 aria-label="Next track"><svg viewBox="0 0 24 24" aria-hidden="true"><path
 d="M15.8 5H18v14h-2.2zM4 5.5v13L14.4 12z"/></svg></button>
<button type="button" class="npbtn" id="np-stop" title="Stop" aria-label="Stop"
 ><svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6.5" y="6.5" width="11"
 height="11" rx="1.2"/></svg></button>
</aside>
<script>
/* The pinned player. It survives navigation because every page carries it and
   it asks this app (which asks Jellyfin) what is playing right now - after
   pressing "Play in Jellyfin" the shelf used to lose sight of the title
   entirely. 5 s is short enough to see a change and long enough not to worry
   the server. */
(function () {
  var bar = document.getElementById('nowplaying');
  if (!bar) return;
  var cover = document.getElementById('np-cover'),
      name = document.getElementById('np-name'),
      sub = document.getElementById('np-sub'),
      fill = document.getElementById('np-fill'),
      toggle = document.getElementById('np-toggle'),
      stop = document.getElementById('np-stop'),
      prev = document.getElementById('np-prev'),
      next = document.getElementById('np-next'),
      rew = document.getElementById('np-rew'),
      fwd = document.getElementById('np-fwd'),
      players = document.getElementById('np-players'),
      now = {session: '', paused: false},
      inFlight = 0,
      quietUntil = 0;

  function hide() {
    bar.hidden = true;
    document.body.classList.remove('has-nowplaying');
  }

  function paint(data) {
    if (!data || !data.playing) {
      hide();
      return;
    }
    now.session = data.session || '';
    now.paused = !!data.paused;
    if (data.cover) { cover.src = data.cover; }
    else { cover.removeAttribute('src'); }
    name.textContent = data.title || data.name || '';
    if (data.href) { name.href = data.href; }
    else { name.removeAttribute('href'); }
    sub.textContent = [data.subtitle, data.device].filter(Boolean).join(' \u00b7 ');
    var pct = 0;
    if (data.duration) {
      pct = Math.max(0, Math.min(100, (data.position / data.duration) * 100));
    }
    fill.style.width = pct + '%';
    // one button, two faces - the label says what a press will do
    var label = now.paused ? 'Play' : 'Pause';
    toggle.title = label;
    toggle.setAttribute('aria-label', label);
    bar.classList.toggle('paused', now.paused);
    // a track gets previous/next; a film only scrubs
    bar.classList.toggle('music', (data.kind || '') === 'audio');
    paintPlayers(data.sessions || []);
    bar.hidden = false;
    document.body.classList.add('has-nowplaying');
  }

  // One pill per player with something on. Pressing one hands the controls
  // over: the next poll asks for that session and every button acts on it.
  function paintPlayers(list) {
    players.innerHTML = '';
    if (list.length < 2) {
      players.hidden = true;
      return;
    }
    list.forEach(function (item) {
      var pill = document.createElement('button');
      pill.type = 'button';
      pill.textContent = item.device || 'Jellyfin app';
      pill.title = (item.title ? item.title + ' on ' : '')
        + (item.device || 'Jellyfin app');
      pill.className = [item.session === now.session ? 'current' : '',
                        item.paused ? 'paused' : '']
                        .filter(Boolean).join(' ');
      pill.addEventListener('click', function () { watch(item.session); });
      players.appendChild(pill);
    });
    players.hidden = false;
  }

  function watch(session) {
    now.session = session;      // Stop and Pause act on this one straight away
    poll();
  }

  function poll() {
    if (inFlight) return;
    if (Date.now() < quietUntil) return;   // a stop is still landing
    var url = 'nowplaying'
      + (now.session ? '?session=' + encodeURIComponent(now.session) : '');
    fetch(url, {headers: {'Accept': 'application/json'}, cache: 'no-store'})
      .then(function (r) { return r.json(); })
      .then(paint)
      .catch(function () { bar.hidden = true; });
  }

  function press(action) {
    inFlight += 1;
    var body = new URLSearchParams();
    body.set('action', action);
    body.set('session_id', now.session);
    if (action === 'stop') {
      // stopping closes the box at once rather than waiting for the next poll,
      // and the poller holds off briefly so it cannot flash straight back
      hide();
      quietUntil = Date.now() + 4000;
    }
    fetch('nowplaying', {method: 'POST', body: body,
                                 headers: {'Accept': 'application/json'}})
      .then(function (r) { return r.json(); })
      .then(function (data) {
        inFlight -= 1;
        if (data && data.note) { sub.textContent = data.note; }
        poll();
      })
      .catch(function () { inFlight -= 1; poll(); });
  }

  toggle.addEventListener('click', function () {
    press(now.paused ? 'resume' : 'pause');
  });
  stop.addEventListener('click', function () { press('stop'); });
  prev.addEventListener('click', function () { press('previous'); });
  next.addEventListener('click', function () { press('next'); });
  rew.addEventListener('click', function () { press('rewind'); });
  fwd.addEventListener('click', function () { press('forward'); });
  poll();
  setInterval(poll, 5000);
})();
</script>
"""


def pick_cover(base, jf_id, poster_url, art_source=""):
    """The image to show for a title: a chosen one first, else Jellyfin, else the poster.

    art_source is empty for titles nobody has chosen art for, which keeps the old
    behaviour (a Jellyfin still beats the fetched poster).
    """
    still = base + "art/" + jf_id if jf_id else ""
    if art_source == "poster" and poster_url:
        return poster_url
    if art_source == "jellyfin" and still:
        return still
    return still or (poster_url or "")


def page(base, title, content, back="",
         back_label="\u2190 Back to the catalogue"):
    return (SHELL.replace("@@BASE@@", html.escape(base))
                 .replace("@@BACK@@", html.escape(base + back))
                 .replace("@@BACKLABEL@@", html.escape(back_label))
                 .replace("@@NOWPLAYING@@", NOW_PLAYING)
                 .replace("@@ADMIN@@", html.escape(base + "admin"))
                 .replace("@@TITLE@@", html.escape(title))
                 .replace("@@CONTENT@@", content))
