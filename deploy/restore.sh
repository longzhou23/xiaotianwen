#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/common-deploy.sh"

require_linux
need tar
need rsync

archive=${1:-}
[[ -f "$archive" ]] || die 'usage: restore.sh /path/to/backup.tar.gz'
staging=$(mktemp -d "${TMPDIR:-/tmp}/xiaotianwen-restore.XXXXXX")
trap 'rm -rf "$staging"' EXIT

tar -xzf "$archive" -C "$staging"
mkdir -p "$PRIVATE_DIR"
rsync -a "$staging"/ "$PRIVATE_DIR"/
log "restore staged under $PRIVATE_DIR"
log 'run install.sh and perform read-only database/provider checks before starting services'
