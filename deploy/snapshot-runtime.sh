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

archive_name=$(basename -- "$archive")
tar_args=(
  -czf "$archive"
  --exclude='*/logs'
  --exclude='*/cache'
  --exclude='*/caches'
  --exclude='*/temp'
  --exclude='*/tmp'
  --exclude='*/thumb_cache'
  --exclude='snowluma/qq-data'
  -C "$RUNTIME_DIR"
  "${snapshot_items[@]}"
)

if ! tar "${tar_args[@]}"; then
  # AstrBot runs as root and may leave private runtime files unreadable by the
  # deploy user.  Retry the exact same read-only archive inside the already
  # pulled AstrBot image, whose root user can read the bind-mounted runtime.
  # This does not chown, rewrite, or delete any runtime data.
  rm -f -- "$archive"
  snapshot_image=${ASTRBOT_IMAGE:-soulter/astrbot:latest}
  docker image inspect "$snapshot_image" >/dev/null 2>&1 \
    || die "runtime snapshot needs a locally available AstrBot image for root-owned files: $snapshot_image"
  log "host tar could not read all runtime files; retrying snapshot in a read-only root container"
  docker run --rm --user 0:0 --entrypoint tar \
    --mount "type=bind,src=$RUNTIME_DIR,dst=/runtime,readonly" \
    --mount "type=bind,src=$BACKUP_ROOT/pre-update,dst=/backup" \
    "$snapshot_image" \
    -czf "/backup/$archive_name" \
    --exclude='*/logs' \
    --exclude='*/cache' \
    --exclude='*/caches' \
    --exclude='*/temp' \
    --exclude='*/tmp' \
    --exclude='*/thumb_cache' \
    --exclude='snowluma/qq-data' \
    -C /runtime "${snapshot_items[@]}"
fi

chmod 600 "$archive"
printf '%s\n' "$archive" >"$DEPLOY_STATE_DIR/last-preupdate-backup"
chmod 600 "$DEPLOY_STATE_DIR/last-preupdate-backup"
log "consistent pre-update runtime snapshot created: $archive"
printf '%s\n' "$archive"
