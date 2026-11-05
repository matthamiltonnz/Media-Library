#!/bin/sh
# Package the add-on, copy it to the Home Assistant host and update the app.
#
#   tools/deploy.sh [hassio@host]
#
# Replaces the old /tmp/h.exp + python-http-server trick: SSH key auth to the
# host works directly, and /tmp on the dev Mac is wiped on reboot.
set -eu

# The address is never committed: pass it, set MC_HOST, or keep it in a
# .deploy-host file beside the repository root (git-ignored).
HOST="${1:-${MC_HOST:-}}"
ROOT=$(cd "$(dirname "$0")/.." && pwd)
if [ -z "$HOST" ] && [ -f "$ROOT/.deploy-host" ]; then
    HOST=$(tr -d '\r\n' < "$ROOT/.deploy-host")
fi
if [ -z "$HOST" ]; then
    echo "usage: tools/deploy.sh hassio@<home-assistant>" >&2
    echo "       or export MC_HOST, or write it into .deploy-host" >&2
    exit 2
fi
TGZ="/tmp/mc-$$.tgz"

cd "$ROOT"
tar czf "$TGZ" --exclude '__pycache__' --exclude '.git' media_collection
VERSION=$(tar xzOf "$TGZ" media_collection/config.yaml \
          | sed -n 's/^version: *//p' | tr -d '"')
echo "packaged media_collection $VERSION"

# `ha apps update` is a no-op when the add-on already runs that number, and a
# deploy then "succeeds" without touching /app - so refuse a repeat up front.
RUNNING=$(ssh -o BatchMode=yes "$HOST" \
  'export SUPERVISOR_TOKEN=$(cat /run/s6/container_environment/HASSIO_TOKEN);
   ha apps info local_media_collection | sed -n "s/^version: *//p"')
if [ "$RUNNING" = "$VERSION" ]; then
  echo "REFUSED - the box already runs $VERSION;" \
       "bump media_collection/config.yaml first" >&2
  exit 1
fi

# scp/sftp is refused by the SSH add-on; stream the tarball over the same
# connection instead.
ssh -o BatchMode=yes "$HOST" 'cat > /tmp/mc.tgz' < "$TGZ"
ssh -o BatchMode=yes "$HOST" '
  set -e
  sudo tar xzf /tmp/mc.tgz -C /addons
  sudo chown -R root:root /addons/media_collection
  export SUPERVISOR_TOKEN=$(cat /run/s6/container_environment/HASSIO_TOKEN)
  ha store reload >/dev/null 2>&1 || true
  ha apps update local_media_collection >/dev/null 2>&1 || true
'
# `ha apps update` returns as soon as the job is queued and `version_latest`
# moves before the image is built, so poll the *installed* version instead -
# the 0.23.75 deploy reported success while the 0.23.74 container was still
# the one answering. A local build takes minutes, hence the long wait (0.23.79
# needed a manual `ha apps update` after the 2-minute window ran out).
INSTALLED=""
tries=0
while [ "$tries" -lt 90 ]; do
  INSTALLED=$(ssh -o BatchMode=yes "$HOST" \
    'export SUPERVISOR_TOKEN=$(cat /run/s6/container_environment/HASSIO_TOKEN);
     ha apps info local_media_collection | sed -n "s/^version: *//p"')
  if [ "$INSTALLED" = "$VERSION" ]; then
    break
  fi
  tries=$((tries + 1))
  sleep 4
done
echo "installed: ${INSTALLED:-unknown} (wanted $VERSION)"
if [ "$INSTALLED" != "$VERSION" ]; then
  echo "MISMATCH - the add-on is still running ${INSTALLED:-nothing}" >&2
  exit 1
fi
echo "deployed $VERSION"
