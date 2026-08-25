#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/common-deploy.sh"

require_linux
require_layout
need git
need python3
need awk

INSTALL_SYSTEM_DEPS=${INSTALL_SYSTEM_DEPS:-0}
if [[ "$INSTALL_SYSTEM_DEPS" == 1 ]]; then
  need sudo
  sudo apt-get update
  sudo apt-get install -y ca-certificates git rsync python3 python3-venv python3-pip docker.io docker-compose-plugin
fi

need rsync

mkdir -p "$RUNTIME_DIR" "$RUNTIME_DIR/astrobot/data" "$RUNTIME_DIR/snowluma/data"

# 实例数据恢复不使用 --delete，避免覆盖目标机上未纳入仓库的文件。
if [[ -d "$PRIVATE_DIR/instance/astrobot-data" ]]; then
  rsync -a "$PRIVATE_DIR/instance/astrobot-data"/ "$RUNTIME_DIR/astrobot/data"/
fi
if [[ -d "$PRIVATE_DIR/instance/snowluma-data/data" ]]; then
  rsync -a "$PRIVATE_DIR/instance/snowluma-data/data"/ "$RUNTIME_DIR/snowluma/data"/
fi

mkdir -p "$RUNTIME_DIR/astrobot/data/plugins"
LOCK_FILE="$PRIVATE_DIR/plugins.lock.yaml"
[[ -f "$LOCK_FILE" ]] || die "plugin lock not found: $LOCK_FILE"

# 读取本项目生成的受限 YAML 形状：每项包含 source 和 enabled。
# 不接受未知根路径，避免锁文件把任意宿主机目录复制进运行时。
mapfile -t enabled_sources < <(awk '
  /^  - name:/ { if (source != "" && enabled == "true") print source; source=""; enabled=""; next }
  /^    source:/ { source=$2; next }
  /^    enabled:/ { enabled=$2; next }
  END { if (source != "" && enabled == "true") print source }
' "$LOCK_FILE")
((${#enabled_sources[@]} > 0)) || die "plugin lock contains no enabled plugins: $LOCK_FILE"

for source_rel in "${enabled_sources[@]}"; do
  case "$source_rel" in
    public/*)
      source_dir="$PUBLIC_DIR/${source_rel#public/}"
      ;;
    private/*)
      source_dir="$PRIVATE_DIR/${source_rel#private/}"
      ;;
    *)
      die "unsupported plugin source in lock: $source_rel"
      ;;
  esac
  [[ -d "$source_dir" ]] || die "locked plugin source not found: $source_dir"
  rsync -a "$source_dir" "$RUNTIME_DIR/astrobot/data/plugins/"
  log "plugin restored: ${source_dir##*/}"
done

if [[ -f "$SECRET_FILE" ]]; then
  chmod 600 "$SECRET_FILE"
  log "secret file present: $SECRET_FILE"
else
  log "secret file not present; code/data layout installed, service start is deferred"
fi

log "installation layout prepared under $PROJECT_ROOT"
log "AstrBot core installation and systemd registration require explicit target-version confirmation"
