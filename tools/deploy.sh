#!/bin/sh
# Package the add-on, copy it to the Home Assistant host and update the app.
#
#   tools/deploy.sh [hassio@host]
#
# Replaces the old /tmp/h.exp + python-http-server trick: SSH key auth to the
# host works directly, and /tmp on the dev Mac is wiped on reboot.
set -eu

HOST="${1:-hassio@192.168.234.3}"
ROOT=$(cd "$(dirname "$0")/.." && pwd)
TGZ="/tmp/mc-$$.tgz"

cd "$ROOT"
tar czf "$TGZ" --exclude '__pycache__' --exclude '.git' media_collection
VERSION=$(tar xzOf "$TGZ" media_collection/config.yaml \
          | sed -n 's/^version: *//p' | tr -d '"')
echo "packaged media_collection $VERSION"

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
sleep 20
INSTALLED=$(ssh -o BatchMode=yes "$HOST" \
  'export SUPERVISOR_TOKEN=$(cat /run/s6/container_environment/HASSIO_TOKEN);
   ha apps info local_media_collection | sed -n "s/^version_latest: *//p"')
echo "version_latest: ${INSTALLED:-unknown}"
if [ "$INSTALLED" != "$VERSION" ]; then
  echo "MISMATCH - the add-on did not pick up $VERSION" >&2
  exit 1
fi
echo "deployed $VERSION"
