#!/usr/bin/env python3
"""Check that this repository is installable as a Home Assistant add-on repo.

This is the check that would have caught the `aarch64` bug fixed in 0.23.99:
`build.yaml` named `ghcr.io/home-assistant/aarch64-base:3.2`, a tag that does
not exist, so every ARM box (Home Assistant Green, Yellow, a Raspberry Pi, an
ARM virtual machine) failed to build the add-on while the amd64 machine the
add-on was developed on built it happily.

    python3 tools/check_addon_repo.py            # everything, asks ghcr.io
    python3 tools/check_addon_repo.py --offline  # skip the registry lookups

Exit code 0 when nothing is wrong, 1 otherwise. Standard library only: it runs
on the Python 3 that ships with the add-on, with no `pip install` first.

What it insists on:

- `repository.yaml` has a name, a url and a real maintainer (no `example.com`);
- `media_collection/config.yaml` has the keys the Supervisor requires, a slug
  matching the folder, a three-part version, an `arch` list, and no option that
  the `schema` does not describe;
- `build.yaml` builds every one of those architectures, from base images that
  actually exist in the Home Assistant registry;
- the store artwork is the size Home Assistant asks for (icon 128x128, logo
  250x100);
- the README still carries its *Add repository to Home Assistant* links, and
  they point at this repository, so the setup instructions cannot go stale.
"""

import argparse
import json
import os
import re
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_DIR = "media_collection"
ADDON = os.path.join(ROOT, ADDON_DIR)

# Architectures the Supervisor knows about, with the base image each uses.
ARCH_IMAGES = {
    "aarch64": "ghcr.io/home-assistant/aarch64-base",
    "amd64": "ghcr.io/home-assistant/amd64-base",
    "armv7": "ghcr.io/home-assistant/armv7-base",
    "armhf": "ghcr.io/home-assistant/armhf-base",
    "i386": "ghcr.io/home-assistant/i386-base",
}

ICON_SIZE = (128, 128)
LOGO_SIZE = (250, 100)

PLACEHOLDERS = re.compile(r"example\.(com|org)|CHANGEME|TODO|YOUR[-_ ]?NAME", re.I)


# --- a very small YAML reader ------------------------------------------------
# The files checked here are hand-written and flat: mappings, block sequences
# of scalars, and folded or literal blocks. Anything fancier raises a
# ValueError naming the line, which is far better than guessing quietly.
# Scalars come back as written - `false` is the string "false", not False - so
# check a boolean field with `str(value).lower()`, never with `if value:`.

def _read_yaml(path):
    lines = []
    with open(path, "r", encoding="utf-8") as handle:
        for number, raw in enumerate(handle, 1):
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            if "\t" in raw[:indent + 1]:
                raise ValueError("%s:%d: tabs are not indentation" % (path, number))
            lines.append((raw.strip(), indent, number))
    if not lines:
        return {}
    value, _ = _parse_block(lines, 0, lines[0][1], path)
    return value


def _scalar(text):
    text = text.strip()
    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    return text


def _parse_block(lines, index, indent, path):
    if lines[index][1] == indent and lines[index][0].startswith("- "):
        items = []
        while index < len(lines) and lines[index][1] == indent and lines[index][0].startswith("- "):
            text = lines[index][0][2:].strip()
            key, sep, rest = text.partition(":")
            if sep and not text.startswith(("http:", "https:")):
                items.append({_scalar(key): _scalar(rest)})
            else:
                items.append(_scalar(text))
            index += 1
        return items, index

    mapping = {}
    while index < len(lines) and lines[index][1] == indent:
        text, _, number = lines[index]
        key, sep, rest = text.partition(":")
        if not sep:
            raise ValueError("%s:%d: expected `key: value`" % (path, number))
        key = _scalar(key)
        rest = rest.strip()
        index += 1
        if rest in (">", ">-", "|", "|-"):
            block = []
            while index < len(lines) and lines[index][1] > indent:
                block.append(lines[index][0])
                index += 1
            joiner = " " if rest.startswith(">") else "\n"
            mapping[key] = joiner.join(part.strip() for part in block).strip()
            continue
        if rest:
            mapping[key] = _scalar(rest)
            continue
        if index < len(lines) and lines[index][1] > indent:
            mapping[key], index = _parse_block(lines, index, lines[index][1], path)
        else:
            mapping[key] = None
    return mapping, index


# --- helpers -----------------------------------------------------------------

def _png_size(path):
    with open(path, "rb") as handle:
        head = handle.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        raise ValueError("%s is not a PNG" % os.path.basename(path))
    return struct.unpack(">II", head[16:24])


def _ghcr_manifest(image):
    """True/False when ghcr.io answers, None when it cannot be asked."""
    host, _, path = image.partition("/")
    name, _, tag = path.rpartition(":")
    if host != "ghcr.io" or not name or not tag:
        return None
    token_url = "https://ghcr.io/token?service=ghcr.io&scope=repository:%s:pull" % name
    accept = ("application/vnd.oci.image.index.v1+json,"
              "application/vnd.docker.distribution.manifest.list.v2+json,"
              "application/vnd.docker.distribution.manifest.v2+json")
    try:
        with urllib.request.urlopen(token_url, timeout=20) as response:
            token = json.load(response)["token"]
        request = urllib.request.Request(
            "https://ghcr.io/v2/%s/manifests/%s" % (name, tag),
            headers={"Authorization": "Bearer " + token, "Accept": accept},
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status == 200
    except urllib.error.HTTPError as error:
        return False if error.code == 404 else None
    except (urllib.error.URLError, OSError, KeyError, ValueError):
        return None


def _git_remote():
    import subprocess
    try:
        out = subprocess.check_output(
            ["git", "-C", ROOT, "config", "--get", "remote.origin.url"],
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None
    remote = out.decode("utf-8", "replace").strip()
    remote = re.sub(r"^git@([^:]+):", r"https://\1/", remote)
    return remote[:-4] if remote.endswith(".git") else remote


# --- the checks --------------------------------------------------------------

def check_repository(problems, notes):
    """`repository.yaml` is what the Supervisor reads when the URL is added."""
    path = os.path.join(ROOT, "repository.yaml")
    if not os.path.isfile(path):
        problems.append("repository.yaml is missing: Home Assistant cannot add this "
                        "address as an add-on repository without it")
        return None
    data = _read_yaml(path)
    for key in ("name", "url", "maintainer"):
        if not data.get(key):
            problems.append("repository.yaml: `%s` is missing" % key)
    url = str(data.get("url") or "").rstrip("/")
    if url and not re.match(r"^https?://\S+$", url):
        problems.append("repository.yaml: url is not an http(s) address: %r" % url)
    if PLACEHOLDERS.search(str(data.get("maintainer") or "")):
        problems.append("repository.yaml: maintainer still holds a placeholder: %r"
                        % data.get("maintainer"))
    remote = _git_remote()
    if url and remote and url.lower() != remote.rstrip("/").lower():
        notes.append("repository.yaml url %s differs from this clone's remote %s"
                     % (url, remote))
    return data


def check_config(problems, notes):
    """The add-on manifest: the keys the Supervisor requires, plus coherence."""
    path = os.path.join(ADDON, "config.yaml")
    if not os.path.isfile(path):
        problems.append("%s/config.yaml is missing" % ADDON_DIR)
        return None
    data = _read_yaml(path)
    for key in ("name", "version", "slug", "description", "arch"):
        if not data.get(key):
            problems.append("config.yaml: `%s` is missing" % key)
    slug = str(data.get("slug") or "")
    if slug and slug != ADDON_DIR:
        problems.append("config.yaml: slug %r does not match the folder %r - the "
                        "Supervisor builds from the folder name" % (slug, ADDON_DIR))
    version = str(data.get("version") or "")
    if version and not re.match(r"^\d+\.\d+\.\d+$", version):
        problems.append("config.yaml: version %r is not MAJOR.MINOR.PATCH" % version)
    arch = data.get("arch") or []
    if not isinstance(arch, list) or not arch:
        problems.append("config.yaml: `arch` must list at least one architecture")
        arch = []
    for name in arch:
        if name not in ARCH_IMAGES:
            problems.append("config.yaml: unknown architecture %r" % name)
    image = data.get("image")
    if image and "{arch}" not in str(image):
        problems.append("config.yaml: `image` must carry {arch}: %r" % image)
    options = data.get("options") or {}
    schema = data.get("schema") or {}
    if isinstance(options, dict) and isinstance(schema, dict):
        for key in options:
            if key not in schema:
                problems.append("config.yaml: option %r has no schema entry" % key)
        for key in schema:
            if key not in options:
                notes.append("config.yaml: schema describes %r with no default" % key)
        translations = os.path.join(ADDON, "translations", "en.yaml")
        if options and not os.path.isfile(translations):
            notes.append("config.yaml: no translations/en.yaml, so the Configuration "
                         "tab shows the raw option keys")
        elif options:
            described = _read_yaml(translations).get("configuration") or {}
            for key in options:
                if not isinstance(described, dict) or key not in described:
                    notes.append("translations/en.yaml: nothing for option %r" % key)
    return data


def check_build(config, problems, notes, offline):
    """`build.yaml` decides which base image each architecture is built on."""
    path = os.path.join(ADDON, "build.yaml")
    if not os.path.isfile(path):
        notes.append("build.yaml is absent: the Supervisor uses the Dockerfile's "
                     "own BUILD_FROM")
        return
    data = _read_yaml(path)
    build_from = data.get("build_from") or {}
    if not isinstance(build_from, dict):
        problems.append("build.yaml: `build_from` is not a mapping of architecture "
                        "to image")
        return
    wanted = (config or {}).get("arch") or []
    for name in wanted:
        if name not in build_from:
            problems.append("build.yaml: no base image for %r, which config.yaml "
                            "offers" % name)
    for name in build_from:
        if name not in ARCH_IMAGES:
            problems.append("build.yaml: unknown architecture %r" % name)
            continue
        if wanted and name not in wanted:
            notes.append("build.yaml builds %r, which config.yaml does not offer" % name)
        if not str(build_from[name]).startswith(ARCH_IMAGES[name]):
            notes.append("build.yaml: %s builds from %s (usually %s)"
                         % (name, build_from[name], ARCH_IMAGES[name]))
    if offline:
        notes.append("base images not asked about (--offline)")
        return
    for name in sorted(build_from):
        image = str(build_from[name])
        answer = _ghcr_manifest(image)
        if answer is True:
            notes.append("base image %s exists" % image)
        elif answer is False:
            problems.append("build.yaml: %s does not exist in ghcr.io, so %r cannot "
                            "build - the add-on is not installable on that machine"
                            % (image, name))
        else:
            notes.append("could not ask ghcr.io about %s" % image)


def check_files(problems, notes):
    for name in ("Dockerfile", "run.sh", "migrate.sh", "DOCS.md"):
        if not os.path.isfile(os.path.join(ADDON, name)):
            problems.append("%s/%s is missing" % (ADDON_DIR, name))
    for name in ("app", "migrations"):
        if not os.path.isdir(os.path.join(ADDON, name)):
            problems.append("%s/%s/ is missing" % (ADDON_DIR, name))
    for name in ("run.sh", "migrate.sh"):
        path = os.path.join(ADDON, name)
        if os.path.isfile(path) and not os.access(path, os.X_OK):
            notes.append("%s/%s is not executable here; the Dockerfile chmods it"
                         % (ADDON_DIR, name))


def check_artwork(problems):
    for name, size in (("icon.png", ICON_SIZE), ("logo.png", LOGO_SIZE)):
        path = os.path.join(ADDON, name)
        if not os.path.isfile(path):
            problems.append("%s/%s is missing: the store shows a blank tile "
                            "(run tools/make_icons.py)" % (ADDON_DIR, name))
            continue
        try:
            actual = _png_size(path)
        except ValueError as error:
            problems.append(str(error))
            continue
        if actual != size:
            problems.append("%s/%s is %dx%d, expected %dx%d"
                            % (ADDON_DIR, name, actual[0], actual[1], size[0], size[1]))


def check_install_links(repository, problems, notes):
    """The README must keep the one-click links the setup instructions promise."""
    path = os.path.join(ROOT, "README.md")
    if not os.path.isfile(path):
        problems.append("README.md is missing: there are no setup instructions")
        return
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    for redirect in ("supervisor_add_addon_repository", "supervisor_app"):
        if redirect not in text:
            problems.append("README.md: no my.home-assistant.io %r link" % redirect)
    url = str((repository or {}).get("url") or "").rstrip("/")
    if url:
        quoted = urllib.parse.quote(url, safe="")
        if quoted not in text:
            problems.append("README.md: the install links do not carry this "
                            "repository's url (%s)" % url)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offline", action="store_true",
                        help="skip the ghcr.io base image lookups")
    args = parser.parse_args(argv)

    problems, notes = [], []
    repository = check_repository(problems, notes)
    config = check_config(problems, notes)
    check_build(config, problems, notes, args.offline)
    check_files(problems, notes)
    check_artwork(problems)
    check_install_links(repository, problems, notes)

    for note in notes:
        print("note: %s" % note)
    for problem in problems:
        print("FAIL: %s" % problem)
    if problems:
        print("\n%d problem(s)." % len(problems))
        return 1
    print("ok: this repository is a valid Home Assistant add-on repository.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
