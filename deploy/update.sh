#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/common-deploy.sh"

require_linux
require_layout
need git

for repo in "$PUBLIC_DIR" "$PRIVATE_DIR"; do
  [[ -z "$(git -C "$repo" status --porcelain)" ]] || die "working tree is dirty: $repo"
  git -C "$repo" rev-parse --verify HEAD >/dev/null || die "repository has no valid HEAD: $repo"
  git -C "$repo" pull --ff-only
done

exec "$PUBLIC_DIR/deploy/up-latest.sh"
