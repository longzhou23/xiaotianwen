#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/common-deploy.sh"

require_linux
require_docker
need tar

if container_running astrbot || container_running snowluma; then
  die 'runtime snapshot requires stopped astrbot and snowluma containers'
fi

mkdir -p "$BACKUP_ROOT/pre-update" "$DEPLOY_STATE_DIR"
chmod 700 "$BACKUP_ROOT" "$BACKUP_ROOT/pre-update"
stamp=$(date '+%Y%m%d-%H%M%S')
archive="$BACKUP_ROOT/pre-update/runtime-$stamp.tar.gz"
snapshot_items=(astrobot snowluma)
[[ -f "$INSTANCE_MARKER" ]] && snapshot_items+=(.deploy/instance-restored)

tar -czf "$archive" \
  --exclude='*/logs' \
  --exclude='*/cache' \
  --exclude='*/caches' \
  --exclude='*/temp' \
  --exclude='*/tmp' \
  --exclude='*/thumb_cache' \
  --exclude='snowluma/qq-data' \
  -C "$RUNTIME_DIR" "${snapshot_items[@]}"

chmod 600 "$archive"
printf '%s\n' "$archive" >"$DEPLOY_STATE_DIR/last-preupdate-backup"
chmod 600 "$DEPLOY_STATE_DIR/last-preupdate-backup"
log "consistent pre-update runtime snapshot created: $archive"
printf '%s\n' "$archive"
