"""Check every recorded file and forget the ones that are gone.

Runs in the background so the admin page can show how far along it is.
"""
import os
import sqlite3
import threading
import time

import jellyfin

_LOCK = threading.Lock()
STATE = {"running": False, "total": 0, "checked": 0, "gone": 0,
         "routes": 0, "verified": 0, "skipped": [], "phase": "", "pruned": 0,
         "started": "", "finished": ""}


def status():
    return dict(STATE)


def start(db_path):
    with _LOCK:
        if STATE["running"]:
            return False
        STATE.update({"running": True, "total": 0, "checked": 0, "gone": 0,
                      "routes": 0, "verified": 0, "skipped": [], "phase": "",
                      "pruned": 0,
                      "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "finished": ""})
    threading.Thread(target=_run, args=(db_path,), daemon=True).start()
    return True


def _drop_dead_routes(con):
    """Forget Jellyfin routes to items the server no longer holds.

    A route whose item has been deleted from Jellyfin cannot play, and the next
    sync cannot rebuild it from a record that is gone. The route goes, its
    `jellyfin_links` row goes, and the copy is left to `dedupe.orphans()`.

    A server that cannot be asked is left entirely alone: an unreachable box or
    a rejected API key must never look like an empty library. Returns
    (routes dropped, labels of the servers that could not be checked).
    """
    labels = {row["id"]: row["label"] for row in
              con.execute("SELECT id, label FROM jellyfin_servers")}
    grouped = {}
    for row in con.execute(
            "SELECT id, item_id, server_id, external_id FROM digital_sources"
            " WHERE provider = 'jellyfin' ORDER BY server_id"):
        grouped.setdefault(row["server_id"], []).append(row)
    dropped = 0
    verified = 0
    skipped = []
    for server_id, rows in grouped.items():
        with _LOCK:
            STATE["phase"] = "asking Jellyfin which items it still holds"
        try:
            live = jellyfin.existing_ids(con, server_id,
                                         [r["external_id"] for r in rows])
        except jellyfin.JellyfinError:
            live = None
        if live is None:
            skipped.append(labels.get(server_id) or "server %s" % server_id)
            continue
        for row in rows:
            if row["external_id"] in live:
                verified += 1
                continue
            con.execute("DELETE FROM jellyfin_links WHERE item_id = ?"
                        "   AND jf_id = ?",
                        (row["item_id"], row["external_id"]))
            con.execute("DELETE FROM digital_sources WHERE id = ?", (row["id"],))
            dropped += 1
        con.commit()
    with _LOCK:
        STATE["phase"] = ""
    return dropped, verified, skipped


def _run(db_path):
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        roots = {row["id"]: row["path"] for row in con.execute(
            "SELECT id, path FROM media_roots")}
        # a Jellyfin route without its server is dead weight too
        routes = con.execute(
            "DELETE FROM digital_sources WHERE provider = 'jellyfin'"
            "   AND (server_id IS NULL OR server_id NOT IN"
            "        (SELECT id FROM jellyfin_servers))").rowcount
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
        # a route to an item the server has forgotten cannot play, either
        dead, verified, skipped = _drop_dead_routes(con)
        routes += dead
        pruned = dedupe.orphans(con)
        with _LOCK:
            STATE.update({"gone": len(gone), "routes": routes,
                          "verified": verified, "skipped": skipped,
                          "pruned": pruned})
    finally:
        con.close()
        with _LOCK:
            STATE.update({"running": False,
                          "finished": time.strftime("%Y-%m-%d %H:%M:%S")})
