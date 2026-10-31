"""Play a catalogued file on a Home Assistant media player.

The add-on can only see the same NAS folders Home Assistant serves as the `local`
media source, so a rip is handed to HA as a `media-source://` URI rather than a URL:
HA resolves it and passes whatever the target player understands (cast, DLNA, etc.).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = "http://supervisor/core/api"
MEDIA_ROOT = "/media"                  # HA's `local` media source
CACHE_SECONDS = 30
MEDIA_EXTENSIONS = (".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".m2ts",
                    ".iso", ".wmv", ".mpg", ".mpeg", ".flac", ".mp3", ".m4a",
                    ".aac", ".ogg", ".opus", ".wav", ".wma", ".ape", ".aiff")


class PlaybackError(Exception):
    """Something that stops Home Assistant starting the file."""
_cache = {"at": 0.0, "players": []}
TOKEN_FILES = ("/run/s6/container_environment/SUPERVISOR_TOKEN",
               "/run/s6/container_environment/HASSIO_TOKEN")


def token():
    """The add-on's Supervisor token - env first, then the s6 environment files.

    The base image starts services with a stripped environment, so normally the token
    only exists in /run/s6/container_environment once the Supervisor has set it up.
    """
    for name in ("SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        value = os.environ.get(name)
        if value:
            return value.strip()
    for path in TOKEN_FILES:
        try:
            with open(path, encoding="utf-8") as fh:
                value = fh.read().strip()
        except OSError:
            continue
        if value:
            return value
    return ""


def _call(path, method="GET", payload=None, timeout=10):
    headers = {"Authorization": "Bearer " + token(),
               "Content-Type": "application/json"}
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(API + path, data=data, headers=headers,
                                     method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", "replace")
    return json.loads(body) if body.strip() else {}


def players(refresh=False):
    """[{'entity_id', 'name', 'state'}] for every media_player HA exposes."""
    if not token():
        return []
    if not refresh and time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["players"]
    try:
        states = _call("/states")
    except Exception as exc:                      # keep the page usable
        print("playback: could not list players:", exc, file=sys.stderr)
        return _cache["players"]
    found = []
    for state in states:
        entity_id = state.get("entity_id") or ""
        if not entity_id.startswith("media_player."):
            continue
        if (state.get("state") or "") in ("unavailable", "unknown"):
            continue                      # nothing can be sent to those
        attributes = state.get("attributes") or {}
        found.append({"entity_id": entity_id,
                      "name": attributes.get("friendly_name") or entity_id,
                      "state": state.get("state") or ""})
    found.sort(key=lambda item: item["name"].lower())
    _cache.update(at=time.time(), players=found)
    return found


def media_uri(root_path, rel_path):
    """media-source:// for a file under /media, or None when it sits outside."""
    absolute = os.path.normpath(os.path.join(root_path or "", rel_path or ""))
    if not absolute.startswith(MEDIA_ROOT + "/"):
        return None
    inside = os.path.relpath(absolute, MEDIA_ROOT)
    return "media-source://media_source/local/" + urllib.parse.quote(inside)


def content_type(kind):
    return "music" if kind == "music" else "video"


def _files(absolute):
    """The video/audio files a source points at, in play order."""
    if os.path.isfile(absolute):
        return [absolute]
    found = []
    try:
        for name in sorted(os.listdir(absolute)):
            low = name.lower()
            if low.startswith("sample") or not low.endswith(MEDIA_EXTENSIONS):
                continue
            full = os.path.join(absolute, name)
            if os.path.isfile(full):
                found.append(full)
    except OSError:
        return []
    return found


def _play_one(entity_id, uri, kind, enqueue=None):
    payload = {"entity_id": entity_id, "media_content_id": uri,
               "media_content_type": content_type(kind)}
    if enqueue:
        payload["enqueue"] = enqueue
    _call("/services/media_player/play_media", "POST", payload)


def play(entity_id, root_path, rel_path, kind="music"):
    """Start the file behind a source, queueing the rest of the folder.

    Home Assistant's `local` media source resolves files, not folders, so a
    source that points at an album or a film folder starts its first track and
    adds the rest of it to the queue where the player has one.
    """
    if not token():
        raise PlaybackError("Home Assistant API access is not enabled for this add-on.")
    if not entity_id:
        raise PlaybackError("Pick a player first.")
    absolute = os.path.normpath(os.path.join(root_path or "", rel_path or ""))
    files = _files(absolute)
    if not files:
        raise PlaybackError("%s holds no video or audio file, so there is nothing "
                            "to play." % (rel_path or absolute))
    uris = []
    for name in files:
        uri = media_uri(os.path.dirname(name), os.path.basename(name))
        if uri is None:
            raise PlaybackError("%s is outside /media, so Home Assistant cannot read it."
                                % name)
        uris.append(uri)
    try:
        _play_one(entity_id, uris[0], kind)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise PlaybackError("Home Assistant refused that (%s): %s" % (exc.code, detail))
    except Exception as exc:
        raise PlaybackError("Could not reach Home Assistant: %s" % exc)

    queued = 0
    for uri in uris[1:]:
        try:
            _play_one(entity_id, uri, kind, enqueue="add")
            queued += 1
        except Exception:
            break                                # no queue on this player
    if queued:
        return "Playing %s and %d more from %s" % (
            os.path.basename(files[0]), queued, rel_path)
    return "Playing %s" % os.path.basename(files[0])


def last_player(con):
    try:
        row = con.execute("SELECT value FROM settings WHERE key = 'player'").fetchone()
        return row["value"] if row else ""
    except Exception:
        return ""


def remember_player(con, entity_id):
    try:
        con.execute("INSERT INTO settings (key, value) VALUES ('player', ?)"
                    " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (entity_id,))
        con.commit()
    except Exception:
        pass
