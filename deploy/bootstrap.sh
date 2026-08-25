#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/common-deploy.sh"

require_linux
need git
need sudo

PUBLIC_REPO_URL=${PUBLIC_REPO_URL:-}
PRIVATE_REPO_URL=${PRIVATE_REPO_URL:-}
[[ -n "$PUBLIC_REPO_URL" ]] || die 'set PUBLIC_REPO_URL before bootstrap'
[[ -n "$PRIVATE_REPO_URL" ]] || die 'set PRIVATE_REPO_URL before bootstrap'

mkdir -p "$PROJECT_ROOT"
clone_with_optional_token "$PUBLIC_REPO_URL" "$PUBLIC_DIR"
clone_with_optional_token "$PRIVATE_REPO_URL" "$PRIVATE_DIR"

exec "$PUBLIC_DIR/deploy/install.sh"
