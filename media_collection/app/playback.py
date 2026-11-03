"""Play a catalogued file on a Home Assistant media player.

The add-on can only see the same NAS folders Home Assistant serves as the `local`
media source, so a rip is handed to HA as a `media-source://` URI rather than a URL:
HA resolves it and passes whatever the target player understands (cast, DLNA, etc.).
"""
import base64
import json
import os
import socket
import struct
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
PLAY_MEDIA = 512                      # media_player feature bit for play_media
TURN_ON = 128                         # ... and for turn_on
SELECT_SOURCE = 4                     # ... and for select_source (an app)
ACTIVE_STATES = ("playing", "paused", "buffering", "idle", "on")


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


PLAYER_DOMAINS = ("media_player", "remote", "input_select", "script",
                  "automation")


def entities(refresh=False):
    """Every entity a store could be opened on, for the admin picker.

    Wider than players(): a shop's player is not always a media_player. An
    Android/Google TV is often a `remote` (and its app launch is a script), a
    disc player is an `input_select` that fires an automation, and either can be
    a script. Each entry carries its integration, so the list can say which one
    is the Apple TV and which is a Chromecast.
    """
    if not token():
        return []
    try:
        states = _call("/states")
    except Exception as exc:
        print("playback: could not list entities:", exc, file=sys.stderr)
        return []
    registry = integrations()
    found = []
    for state in states:
        entity_id = state.get("entity_id") or ""
        if entity_id.split(".", 1)[0] not in PLAYER_DOMAINS:
            continue
        if (state.get("state") or "") in ("unavailable", "unknown"):
            continue
        attributes = state.get("attributes") or {}
        found.append({
            "entity_id": entity_id,
            "name": attributes.get("friendly_name") or entity_id,
            "state": state.get("state") or "",
            "domain": entity_id.split(".", 1)[0],
            "integration": registry.get(entity_id, ""),
            "sources": [str(app) for app in
                        (attributes.get("source_list") or [])],
        })
    found.sort(key=lambda item: item["name"].lower())
    return found


def entity_name(entity_id):
    """The friendly name of one entity, for a player added without a name."""
    for entity in entities():
        if entity["entity_id"] == entity_id:
            return entity["name"]
    return entity_id


def players(refresh=False):
    """[{'entity_id', 'name', 'state', 'device_class'}] for usable media_players.

    Only players that accept `play_media` are kept - a TV that can only be turned
    up and down cannot take a file - and anything unavailable is left out so the
    picker never offers a device that will fail.
    """
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
    registry = integrations()       # {entity_id: integration}, cached
    for state in states:
        entity_id = state.get("entity_id") or ""
        if not entity_id.startswith("media_player."):
            continue
        if (state.get("state") or "") in ("unavailable", "unknown"):
            continue                      # nothing can be sent to those
        attributes = state.get("attributes") or {}
        if not (attributes.get("supported_features") or 0) & PLAY_MEDIA:
            continue                      # cannot take a file, only volume etc.
        found.append({"entity_id": entity_id,
                      "name": attributes.get("friendly_name") or entity_id,
                      "state": state.get("state") or "",
                      "device_class": attributes.get("device_class") or "",
                      "sources": [str(app) for app in
                                  (attributes.get("source_list") or [])],
                      "can_select": bool(
                          (attributes.get("supported_features") or 0)
                          & SELECT_SOURCE),
                      "integration": registry.get(entity_id, "")})
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


def chosen(con):
    """The players this household picked in Admin, or None for 'no preference'."""
    try:
        row = con.execute("SELECT value FROM settings"
                          " WHERE key = 'playback_players'").fetchone()
        picked = json.loads(row["value"]) if row and row["value"] else None
        return [str(name) for name in picked] if picked else None
    except (TypeError, ValueError, AttributeError):
        return None


def choose(con, entity_ids):
    """Remember the players worth offering; an empty list means all of them."""
    con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                ("playback_players", json.dumps(sorted(set(entity_ids)))))
    con.commit()


def players_for(con):
    """The picker's list: what HA offers, narrowed to the chosen players.

    Anything playing or paused comes first, then everything else by name, so the
    device someone is actually using sits at the top.
    """
    found = players()
    picked = chosen(con)
    if picked:
        wanted = set(picked)
        found = [player for player in found if player["entity_id"] in wanted]
    return sorted(found, key=lambda player: (player["state"] not in ACTIVE_STATES,
                                             player["name"].lower()))


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


def open_on(target, app_name="", timeout=8):
    """Turn a player on and open the app on it. Returns the sentence.

    A player is whatever `playback_targets.kind` says it is:

      media_player   media_player.turn_on, then select_source(app)
      input_select   input_select.select_option - how an automation is fired
                     in most setups ("source: Disc player")
      script         script.turn_on
      automation     automation.trigger

    A bought title cannot be deep-linked, so this opens the app and says so:
    turn the device on, select the app, pick the title there.

    Waking the device raises on failure - a box off the network is worth
    reporting - while a device that will not list its apps only warns, because
    the wake-up still happened.
    """
    entity_id = (target.get("ha_entity_id") or "").strip()
    if not entity_id:
        raise ValueError("that player has no Home Assistant entity set")
    kind = target.get("kind") or "media_player"
    domain = entity_id.split(".", 1)[0]
    label = target.get("name") or entity_id

    if kind == "script" or domain == "script":
        _call("/services/script/turn_on", "POST", {"entity_id": entity_id},
              timeout=timeout)
        return "Ran %s." % label
    if kind == "automation" or domain == "automation":
        _call("/services/automation/trigger", "POST", {"entity_id": entity_id},
              timeout=timeout)
        return "Triggered %s." % label
    if kind == "remote" or domain == "remote":
        _call("/services/remote/turn_on", "POST", {"entity_id": entity_id},
              timeout=timeout)
        return "Woke %s." % label
    if kind == "input_select" or domain == "input_select":
        option = (target.get("input_source") or "").strip()
        if not option:
            raise ValueError("that input_select player has no option set")
        _call("/services/input_select/select_option", "POST",
              {"entity_id": entity_id, "option": option}, timeout=timeout)
        return "Set %s to %s." % (label, option)

    _call("/services/media_player/turn_on", "POST", {"entity_id": entity_id},
          timeout=timeout)
    app = (app_name or target.get("input_source") or "").strip()
    if not app:
        return "Woke the %s." % label
    if _is_app_id(app):
        # Android TV and Fire TV launch an app by package id through play_media
        # and publish no source_list at all - select_source cannot work there.
        try:
            _call("/services/media_player/play_media", "POST",
                  {"entity_id": entity_id, "media_content_type": "app",
                   "media_content_id": app}, timeout=timeout)
        except Exception as exc:
            return ("Woke the %s, but could not start %s (%s)."
                    % (label, app, exc))
        return "Woke the %s and started %s." % (label, app)
    try:
        _call("/services/media_player/select_source", "POST",
              {"entity_id": entity_id, "source": app}, timeout=timeout)
    except Exception as exc:
        return ("Woke the %s, but could not open %s (%s) - that device may not "
                "list its apps." % (label, app, exc))
    return "Woke the %s and opened %s." % (label, app)


def _is_app_id(text):
    """Whether an app is named by package id or by the name the device lists.

    `com.linkplay.wiimtv` is an Android package: Android TV and Fire TV launch
    it with media_player.play_media (media_content_type "app") and publish no
    source_list, so select_source cannot reach it. "Apple TV" is a tvOS app name,
    which those devices do list and take through select_source.
    """
    return "." in text and " " not in text


WS_HOST = "supervisor"
WS_PORT = 80
WS_PATH = "/core/websocket"
REGISTRY_SECONDS = 600
_registry = {"at": 0.0, "map": {}}


def _ws_read(sock, count):
    chunks = []
    while count:
        chunk = sock.recv(count)
        if not chunk:
            raise RuntimeError("Home Assistant closed the websocket")
        chunks.append(chunk)
        count -= len(chunk)
    return b"".join(chunks)


def _ws_send(sock, payload):
    data = payload.encode("utf-8")
    mask = os.urandom(4)
    header = bytearray([0x81])                  # text message, FIN set
    length = len(data)
    if length < 126:
        header.append(0x80 | length)
    elif length < 1 << 16:
        header.append(0x80 | 126)
        header += struct.pack("!H", length)
    else:
        header.append(0x80 | 127)
        header += struct.pack("!Q", length)
    sock.sendall(bytes(header) + mask +
                 bytes(byte ^ mask[i % 4] for i, byte in enumerate(data)))


def _ws_recv(sock):
    """One websocket message, joining fragments."""
    message = b""
    while True:
        first, second = _ws_read(sock, 2)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", _ws_read(sock, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", _ws_read(sock, 8))[0]
        mask = _ws_read(sock, 4) if second & 0x80 else b""
        payload = _ws_read(sock, length) if length else b""
        if mask:
            payload = bytes(byte ^ mask[i % 4]
                            for i, byte in enumerate(payload))
        message += payload
        if first & 0x80:
            return message


def integrations(refresh=False):
    """{entity_id: integration} for every entity Home Assistant knows.

    The REST API does not expose the entity registry - `config/entity_registry
    /list` is websocket only - so this speaks just enough of the protocol to ask
    that one question. It is what tells the Stores panel that a player is the
    `apple_tv` integration rather than a Chromecast or a `cast` group.

    Any failure returns {} and is remembered for REGISTRY_SECONDS: a missing
    hint must never take the admin page down.
    """
    if not refresh and time.time() - _registry["at"] < REGISTRY_SECONDS:
        return _registry["map"]
    found = {}
    sock = None
    try:
        sock = socket.create_connection((WS_HOST, WS_PORT), timeout=20)
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall((
            "GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\nAuthorization: Bearer %s\r\n\r\n"
            % (WS_PATH, WS_HOST, key, token())).encode("utf-8"))
        head = b""
        while b"\r\n\r\n" not in head:
            head += _ws_read(sock, 1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise RuntimeError(head.split(b"\r\n")[0].decode("latin-1"))
        _ws_recv(sock)                                    # auth_required
        _ws_send(sock, json.dumps({"type": "auth",
                                   "access_token": token()}))
        _ws_recv(sock)                                    # auth_ok
        _ws_send(sock, json.dumps({"id": 1,
                                   "type": "config/entity_registry/list"}))
        answer = json.loads(_ws_recv(sock).decode("utf-8"))
        for entry in answer.get("result") or []:
            entity_id = entry.get("entity_id") or ""
            if entity_id:
                found[entity_id] = entry.get("platform") or ""
    except Exception as exc:
        print("playback: could not read the entity registry:", exc,
              file=sys.stderr)
        found = {}
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    _registry.update({"at": time.time(), "map": found})
    return found

