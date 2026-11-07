"""Background bulk metadata refresh (one title at a time, politely paced).

Wikimedia throttles bursts, so this runs sequentially in a daemon thread and the
admin page shows progress. Only fills missing fields unless force=True.
"""
import sqlite3
import threading
import time

import metadata

_LOCK = threading.Lock()
STATE = {
    "running": False, "done": 0, "total": 0, "updated": 0, "filed": 0, "failed": 0,
    "throttled": 0, "started": None, "finished": None, "force": False,
    "last": "", "paused": False, "waiting": False, "next_try": 0,
    # the collections half of the pass: every collection's film list, its
    # paragraph and any shelf film the series names that nobody had filed
    "collections": 0, "collection_done": 0, "collection_filed": 0,
}


AUTO = {"hours": 0, "next": 0.0}


def status():
    state = dict(STATE)
    state["auto_hours"] = AUTO["hours"]
    state["auto_next"] = AUTO["next"]
    # The clock the page prints for a pause is formatted **here**, by the same
    # code that formats every other time on that page: a browser in another
    # timezone (or one that prefers 12-hour time) then cannot render the same
    # moment as "12:34 pm" beside the server's "23:34".
    state["next_try_at"] = (time.strftime("%H:%M",
                                         time.localtime(state["next_try"]))
                            if state.get("next_try") else "")
    return state


def start(db_path, force=False, pause=1.2, limit=None, collections=False):
    """Look up the titles that are missing metadata, in the background.

    `limit` bounds a run: the add-on refreshes a handful on every start (the
    button does the whole catalogue), because a shelf can easily hold hundreds
    of titles that no lookup will ever fill and repeating that sweep on each
    restart would keep Wikipedia busy for nothing.

    `collections` adds the other backlog: every collection's film list, its
    paragraph and cover, and any film on the shelf that its series names but
    nobody has filed. It is off at start-up (a restart should not walk fifteen
    series) and on for the button and the nightly pass.
    """
    with _LOCK:
        if STATE["running"]:
            return False
        targets = _targets(db_path, force)
        if limit is not None:
            targets = targets[:limit]
        if not targets and not collections:
            return False
        STATE.update(running=True, force=force, done=0, total=len(targets),
                     updated=0, filed=0, failed=0, throttled=0, last="", paused=False,
                     waiting=False, next_try=0, finished=None,
                     collections=0, collection_done=0, collection_filed=0,
                     started=time.strftime("%Y-%m-%d %H:%M"))
    threading.Thread(target=_run, args=(db_path, targets, pause, collections),
                     daemon=True).start()
    return True


def _targets(db_path, force):
    con = sqlite3.connect(db_path, timeout=30)
    try:
        sql = "SELECT id FROM titles"
        if not force:
            # also revisit titles where an id exists but the stored match came from
            # somewhere else - that is how a wrong film gets corrected
            # an album has no overview to fill - MusicBrainz carries no prose
            # for a release - so testing overview IS NULL queued every album on
            # every run. Music is judged on its id and cover instead.
            sql += (" WHERE (kind = 'music'"
                    "        AND (musicbrainz_id IS NULL"
                    "             OR poster_url IS NULL))"
                    "    OR ((kind IS NULL OR kind <> 'music') AND ("
                    "         overview IS NULL OR poster_url IS NULL"
                    "         OR (imdb_id IS NOT NULL"
                    "             AND COALESCE(metadata_source, '')"
                    "                 NOT LIKE '%imdb%')"
                    "         OR (tmdb_id IS NOT NULL"
                    "             AND COALESCE(metadata_source, '')"
                    "                 NOT LIKE '%tmdb%')))")
        found = [row[0] for row in con.execute(sql + " ORDER BY name")]
        if force:
            return [(title_id, True) for title_id in found]
        # A title whose metadata is complete can still have no collection:
        # the series question costs two or three requests, not a fresh
        # lookup, so it is asked here rather than by overwriting the
        # metadata of the whole shelf.
        series = [row[0] for row in con.execute(
            "SELECT id FROM titles WHERE kind IN ('movie', 'tv')"
            "   AND collection_id IS NULL AND series_at IS NULL"
            " ORDER BY name")]
        seen = set(found)
        wanted = [(title_id, False) for title_id in series
                  if title_id not in seen]
        # Alternate the two kinds of work, so a run that is bounded (the
        # add-on looks up sixty at start-up) does some of each rather than
        # spending the whole of itself on one backlog.
        merged = []
        for index in range(max(len(wanted), len(found))):
            if index < len(wanted):
                merged.append(wanted[index])
            if index < len(found):
                merged.append((found[index], True))
        return merged
    finally:
        con.close()


def _run(db_path, targets, pause, collections=False):
    con = sqlite3.connect(db_path, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 60000")
    con.execute("PRAGMA foreign_keys = ON")
    try:
        wait_seconds = 10 * 60                     # first 429 -> wait 10 minutes
        # Collections first. Each costs a handful of requests, while the title
        # backlog behind it can be hours long - and a pass that is running out
        # of patience with Wikimedia should have done the cheap half already.
        if collections:
            _run_collections(con, pause)
        for title_id, needs_metadata in targets:
            attempts = 0
            while True:
                try:
                    result = (metadata.refresh(con, title_id)
                              if needs_metadata
                              else metadata.fill_series(con, title_id))
                except Exception as exc:          # never kill the job
                    result = "error: %s" % exc

                if "rate-limited" not in result and "locked" not in result:
                    break

                attempts += 1
                if "locked" in result:
                    # the artist job or a page write held the database; that
                    # passes, and a film is not worth losing to it
                    STATE["last"] = "database busy - retrying"
                    if attempts > 4:
                        break
                    time.sleep(5)
                    continue
                STATE["throttled"] += 1
                if attempts > 4:
                    STATE["paused"] = True
                    STATE["last"] = "Wikimedia kept refusing — stopped, press Resume later"
                    break

                STATE["waiting"] = True
                STATE["next_try"] = time.time() + wait_seconds
                STATE["last"] = ("rate limited — retrying at %s"
                                 % time.strftime("%H:%M", time.localtime(STATE["next_try"])))
                while time.time() < STATE["next_try"]:
                    time.sleep(5)                 # keeps the status endpoint live
                STATE["waiting"] = False
                wait_seconds = min(wait_seconds * 2, 60 * 60)

            if STATE["paused"]:
                break

            STATE["done"] += 1
            STATE["last"] = result[:120]
            if result.startswith("metadata refreshed"):
                STATE["updated"] += 1
                wait_seconds = 10 * 60            # reset the backoff after a good one
                time.sleep(pause)
            elif result.startswith("filed under"):
                STATE["filed"] += 1
                wait_seconds = 10 * 60
                time.sleep(pause)
            elif result == "no series":
                time.sleep(pause)
            else:
                STATE["failed"] += 1
                time.sleep(max(pause, 1.0))
    finally:
        con.close()
        STATE["running"] = False
        STATE["finished"] = time.strftime("%Y-%m-%d %H:%M")


def _run_collections(con, pause):
    """The other backlog: each collection's films, paragraph and cover.

    A collection is cheap next to a title - four or five requests - and the
    shelf holds a handful of them, so there is no limit and no ordering to
    interleave: every collection is refreshed on every unbounded pass. What a
    pass cannot carry is a rate limit, so a `Throttle` stops the half where it
    stands and leaves the rest to the next one.
    """
    rows = con.execute("SELECT id FROM collections ORDER BY id").fetchall()
    STATE["collections"] = len(rows)
    for row in rows:
        if STATE["paused"]:
            return
        try:
            summary = metadata.refresh_collection(con, row["id"])
        except metadata.Throttle:
            STATE["paused"] = True
            STATE["last"] = ("Wikimedia kept refusing - the collections "
                             "pass stopped")[:120]
            return
        except Exception as exc:            # one bad series, not the pass
            STATE["failed"] += 1
            STATE["last"] = ("collection %d: %s" % (row["id"], exc))[:120]
            time.sleep(max(pause, 1.0))
            continue
        STATE["collection_done"] += 1
        filed = summary.get("filed") or []
        STATE["collection_filed"] += len(filed)
        if filed:
            STATE["last"] = ("collection %d: filed %s"
                             % (row["id"], ", ".join(filed[:3])))[:120]
        time.sleep(pause)


def _until_hour(hour):
    """Seconds until the next `hour`:00 local time (never less than a minute)."""
    now = time.localtime()
    when = list(now)[:6]
    when[3], when[4], when[5] = int(hour), 0, 0
    target = time.mktime(tuple(when) + (0, 0, -1))
    while target <= time.time():
        target += 24 * 3600
    return max(60.0, target - time.time())


def start_auto(db_path, hours=24, hour_of_day=3, limit=200, also=None):
    """Run the same pass by itself, so a shelf nobody touches still gets tidied.

    `hours` is the interval - 24 daily, 168 weekly - and 0 turns it off. The
    first run waits for the next `hour_of_day` (03:00 local) instead of firing
    at start-up: `main()` already refreshes a bounded handful there, and two
    passes at once is exactly the burst Wikimedia rate-limits. `limit` bounds a
    night's work (a shelf holds hundreds of titles no lookup will ever fill);
    `also` is called after each pass (the artist fill) and is allowed to fail.
    """
    try:
        hours = int(hours)
    except (TypeError, ValueError):
        hours = 0
    AUTO.update(hours=max(0, hours))
    if hours <= 0:
        AUTO["next"] = 0.0
        return False
    threading.Thread(target=_auto, daemon=True,
                     args=(db_path, hours, hour_of_day, limit, also)).start()
    return True


def _auto(db_path, hours, hour_of_day, limit, also):
    while True:
        wait = _until_hour(hour_of_day)
        AUTO["next"] = time.time() + wait
        time.sleep(wait)
        AUTO["next"] = 0.0
        try:
            start(db_path, limit=limit, collections=True)
            if also:
                also()
        except Exception as exc:        # a background pass must never die
            print("refresh_job: automatic pass failed (%s)" % exc)
        if hours != 24:
            # any other interval counts from this run, not from 03:00 again
            AUTO["next"] = time.time() + hours * 3600
            time.sleep(hours * 3600)

