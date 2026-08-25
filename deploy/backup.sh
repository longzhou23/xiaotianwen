#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/common-deploy.sh"

require_linux
require_layout
need tar

mkdir -p "$BACKUP_ROOT"
chmod 700 "$BACKUP_ROOT"
stamp=$(date '+%Y%m%d-%H%M%S')
archive="$BACKUP_ROOT/xiaotianwen-instance-$stamp.tar.gz"

tar -czf "$archive" \
  --exclude='*/logs' \
  --exclude='*/cache' \
  --exclude='*/thumb_cache' \
  --exclude='*/temp' \
  -C "$PRIVATE_DIR" instance knowledge plugins.lock.yaml deployment

chmod 600 "$archive"
log "backup created: $archive"
