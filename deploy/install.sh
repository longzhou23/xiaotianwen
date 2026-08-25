#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/common-deploy.sh"

require_linux
require_layout
need git
need rsync
need python3

INSTALL_SYSTEM_DEPS=${INSTALL_SYSTEM_DEPS:-0}
if [[ "$INSTALL_SYSTEM_DEPS" == 1 ]]; then
  need sudo
  sudo apt-get update
  sudo apt-get install -y ca-certificates git rsync python3 python3-venv python3-pip docker.io docker-compose-plugin
fi

mkdir -p "$RUNTIME_DIR" "$RUNTIME_DIR/astrobot/data" "$RUNTIME_DIR/snowluma/data"

# 实例数据恢复不使用 --delete，避免覆盖目标机上未纳入仓库的文件。
if [[ -d "$PRIVATE_DIR/instance/astrobot-data" ]]; then
  rsync -a "$PRIVATE_DIR/instance/astrobot-data"/ "$RUNTIME_DIR/astrobot/data"/
fi
if [[ -d "$PRIVATE_DIR/instance/snowluma-data/data" ]]; then
  rsync -a "$PRIVATE_DIR/instance/snowluma-data/data"/ "$RUNTIME_DIR/snowluma/data"/
fi

mkdir -p "$RUNTIME_DIR/astrobot/data/plugins"
if [[ -f "$PRIVATE_DIR/plugins.lock.yaml" ]]; then
  log "plugin lock found: $PRIVATE_DIR/plugins.lock.yaml"
else
  die "plugin lock not found: $PRIVATE_DIR/plugins.lock.yaml"
fi

for category in upstream modified custom; do
  source_dir="$PUBLIC_DIR/plugins/$category"
  [[ -d "$source_dir" ]] || continue
  for plugin in "$source_dir"/*; do
    [[ -d "$plugin" ]] || continue
    rsync -a "$plugin" "$RUNTIME_DIR/astrobot/data/plugins/"
  done
done

if [[ -f "$SECRET_FILE" ]]; then
  chmod 600 "$SECRET_FILE"
  log "secret file present: $SECRET_FILE"
else
  log "secret file not present; code/data layout installed, service start is deferred"
fi

log "installation layout prepared under $PROJECT_ROOT"
log "AstrBot core installation and systemd registration require explicit target-version confirmation"
