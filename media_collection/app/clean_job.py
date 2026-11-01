"""Check every recorded file and forget the ones that are gone.

Runs in the background so the admin page can show how far along it is.
"""
import os
import sqlite3
import threading
import time

_LOCK = threading.Lock()
STATE = {"running": False, "total": 0, "checked": 0, "gone": 0,
         "pruned": 0, "started": "", "finished": ""}


def status():
    return dict(STATE)


def start(db_path):
    with _LOCK:
        if STATE["running"]:
            return False
        STATE.update({"running": True, "total": 0, "checked": 0, "gone": 0,
                      "pruned": 0,
                      "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "finished": ""})
    threading.Thread(target=_run, args=(db_path,), daemon=True).start()
    return True


def _run(db_path):
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        roots = {row["id"]: row["path"] for row in con.execute(
            "SELECT id, path FROM media_roots")}
        # a Jellyfin route without its server is dead weight too
        con.execute("DELETE FROM digital_sources WHERE provider = 'jellyfin'"
                    "   AND (server_id IS NULL OR server_id NOT IN"
                    "        (SELECT id FROM jellyfin_servers))")
        # a file row is only real while its folder still exists
        orphans = con.execute(
            "DELETE FROM digital_sources WHERE provider = 'rip'"
            "   AND (root_id IS NULL OR root_id NOT IN"
            "        (SELECT id FROM media_roots))").rowcount
        con.commit()
        rows = con.execute("SELECT id, root_id, path FROM digital_sources"
                           " WHERE provider = 'rip'").fetchall()
        with _LOCK:
            STATE["total"] = len(rows)
        gone = []
        for row in rows:
            where = row["path"]
            if where:
                base_dir = roots.get(row["root_id"])
                full = os.path.join(base_dir, where) if base_dir else where
                try:
                    there = os.path.exists(full)
                except (OSError, TypeError, ValueError):
                    there = False
                if not there:
                    gone.append(row["id"])
            with _LOCK:
                STATE["checked"] += 1
        for start in range(0, len(gone), 400):
            chunk = gone[start:start + 400]
            con.execute("DELETE FROM digital_sources WHERE id IN (%s)"
                        % ",".join("?" * len(chunk)), chunk)
        con.commit()
        import dedupe
        pruned = dedupe.orphans(con)
        with _LOCK:
            STATE.update({"gone": len(gone), "pruned": pruned})
    finally:
        con.close()
        with _LOCK:
            STATE.update({"running": False,
                          "finished": time.strftime("%Y-%m-%d %H:%M:%S")})
