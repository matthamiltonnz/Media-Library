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
    "running": False, "done": 0, "total": 0, "updated": 0, "failed": 0,
    "throttled": 0, "started": None, "finished": None, "force": False,
    "last": "", "paused": False, "waiting": False, "next_try": 0,
}


def status():
    return dict(STATE)


def start(db_path, force=False, pause=0.4, limit=None):
    """Look up the titles that are missing metadata, in the background.

    `limit` bounds a run: the add-on refreshes a handful on every start (the
    button does the whole catalogue), because a shelf can easily hold hundreds
    of titles that no lookup will ever fill and repeating that sweep on each
    restart would keep Wikipedia busy for nothing.
    """
    with _LOCK:
        if STATE["running"]:
            return False
        targets = _targets(db_path, force)
        if limit is not None:
            targets = targets[:limit]
        if not targets:
            return False
        STATE.update(running=True, force=force, done=0, total=len(targets),
                     updated=0, failed=0, throttled=0, last="", paused=False,
                     waiting=False, next_try=0, finished=None,
                     started=time.strftime("%Y-%m-%d %H:%M:%S"))
    threading.Thread(target=_run, args=(db_path, targets, pause), daemon=True).start()
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
        return [row[0] for row in con.execute(sql + " ORDER BY name")]
    finally:
        con.close()


def _run(db_path, targets, pause):
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        wait_seconds = 10 * 60                     # first 429 -> wait 10 minutes
        for title_id in targets:
            attempts = 0
            while True:
                try:
                    result = metadata.refresh(con, title_id)
                except Exception as exc:          # never kill the job
                    result = "error: %s" % exc

                if "rate-limited" not in result:
                    break

                attempts += 1
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
            else:
                STATE["failed"] += 1
                time.sleep(max(pause, 1.0))
    finally:
        con.close()
        STATE["running"] = False
        STATE["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
