#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/common-deploy.sh"

require_linux

PUBLIC_REPO_URL=${PUBLIC_REPO_URL:-}
PRIVATE_REPO_URL=${PRIVATE_REPO_URL:-}
[[ -n "$PUBLIC_REPO_URL" ]] || die 'set PUBLIC_REPO_URL before bootstrap'
[[ -n "$PRIVATE_REPO_URL" ]] || die 'set PRIVATE_REPO_URL before bootstrap'

# 新机默认安装系统依赖；离线/预装环境可显式设置 INSTALL_SYSTEM_DEPS=0。
export INSTALL_SYSTEM_DEPS=${INSTALL_SYSTEM_DEPS:-1}
if [[ "$INSTALL_SYSTEM_DEPS" == 1 ]]; then
  need sudo
  sudo apt-get update
  compose_package=''
  for candidate in docker-compose-v2 docker-compose-plugin docker-compose; do
    if apt-cache show "$candidate" >/dev/null 2>&1; then
      compose_package=$candidate
      break
    fi
  done
  [[ -n "$compose_package" ]] || die 'no Docker Compose package is available from the configured apt repositories'
  sudo apt-get install -y \
    ca-certificates curl git rsync python3 python3-venv python3-pip \
    openssl docker.io "$compose_package"
  sudo systemctl enable --now docker
  if ! id -nG "$(id -un)" | tr ' ' '\n' | grep -qx docker; then
    sudo usermod -aG docker "$(id -un)"
    log "added $(id -un) to docker group"
  fi
fi
need git

mkdir -p "$PROJECT_ROOT"
clone_with_optional_token "$PUBLIC_REPO_URL" "$PUBLIC_DIR"
clone_with_optional_token "$PRIVATE_REPO_URL" "$PRIVATE_DIR"

ensure_onebot_secret_pair

if ! docker info >/dev/null 2>&1; then
  die "Docker is installed but this shell cannot access it; run 'newgrp docker' once, then rerun bootstrap.sh with INSTALL_SYSTEM_DEPS=0"
fi

RESTORE_INSTANCE=${RESTORE_INSTANCE:-auto} "$PUBLIC_DIR/deploy/install.sh"

if [[ "${START_SERVICES:-1}" == 1 ]]; then
  exec "$PUBLIC_DIR/deploy/up-latest.sh"
fi
