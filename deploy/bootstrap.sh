#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/common-deploy.sh"

require_linux
need sudo

PUBLIC_REPO_URL=${PUBLIC_REPO_URL:-}
PRIVATE_REPO_URL=${PRIVATE_REPO_URL:-}
[[ -n "$PUBLIC_REPO_URL" ]] || die 'set PUBLIC_REPO_URL before bootstrap'
[[ -n "$PRIVATE_REPO_URL" ]] || die 'set PRIVATE_REPO_URL before bootstrap'

# 新机默认安装系统依赖；离线/预装环境可显式设置 INSTALL_SYSTEM_DEPS=0。
export INSTALL_SYSTEM_DEPS=${INSTALL_SYSTEM_DEPS:-1}
if [[ "$INSTALL_SYSTEM_DEPS" == 1 ]]; then
  sudo apt-get update
  sudo apt-get install -y ca-certificates git rsync python3 python3-venv python3-pip docker.io docker-compose-plugin
fi
need git

mkdir -p "$PROJECT_ROOT"
clone_with_optional_token "$PUBLIC_REPO_URL" "$PUBLIC_DIR"
clone_with_optional_token "$PRIVATE_REPO_URL" "$PRIVATE_DIR"

exec "$PUBLIC_DIR/deploy/install.sh"
