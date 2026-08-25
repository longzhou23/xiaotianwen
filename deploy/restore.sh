#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/common-deploy.sh"

require_linux
need tar
need rsync
need python3
acquire_deploy_lock

archive=${1:-}
[[ -f "$archive" ]] || die 'usage: restore.sh /path/to/backup.tar.gz'
python3 - "$archive" <<'PY'
import sys
import tarfile
from pathlib import PurePosixPath

with tarfile.open(sys.argv[1], "r:gz") as archive:
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe archive member: {member.name}")
        if member.issym() or member.islnk():
            target = PurePosixPath(member.linkname)
            if target.is_absolute() or ".." in target.parts:
                raise SystemExit(f"unsafe archive link: {member.name} -> {member.linkname}")
PY
staging=$(mktemp -d "${TMPDIR:-/tmp}/xiaotianwen-restore.XXXXXX")
trap 'rm -rf "$staging"' EXIT

tar -xzf "$archive" -C "$staging"
mkdir -p "$PRIVATE_DIR"
rsync -a "$staging"/ "$PRIVATE_DIR"/
log "restore staged under $PRIVATE_DIR"
log 'run install.sh and perform read-only database/provider checks before starting services'
