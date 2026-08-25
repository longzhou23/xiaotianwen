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
  # Ubuntu 24.04 官方源使用 docker-compose-v2；docker-compose-plugin 是 Docker 官方源的包名。
  sudo apt-get install -y ca-certificates git rsync python3 python3-venv python3-pip docker.io docker-compose-v2
fi

need rsync

mkdir -p \
  "$RUNTIME_DIR" \
  "$RUNTIME_DIR/astrobot/data" \
  "$RUNTIME_DIR/snowluma/data" \
  "$RUNTIME_DIR/snowluma/qq-config" \
  "$RUNTIME_DIR/snowluma/qq-data"

# 实例数据恢复不使用 --delete，避免覆盖目标机上未纳入仓库的文件。
if [[ -d "$PRIVATE_DIR/instance/astrobot-data" ]]; then
  rsync -a "$PRIVATE_DIR/instance/astrobot-data"/ "$RUNTIME_DIR/astrobot/data"/
fi
if [[ -d "$PRIVATE_DIR/instance/snowluma-data/data" ]]; then
  rsync -a "$PRIVATE_DIR/instance/snowluma-data/data"/ "$RUNTIME_DIR/snowluma/data"/
fi
if [[ -d "$PRIVATE_DIR/instance/qq-config" ]]; then
  rsync -a "$PRIVATE_DIR/instance/qq-config"/ "$RUNTIME_DIR/snowluma/qq-config"/
fi
if [[ -d "$PRIVATE_DIR/instance/qq-data" ]]; then
  rsync -a "$PRIVATE_DIR/instance/qq-data"/ "$RUNTIME_DIR/snowluma/qq-data"/
fi

# Resolve only the OneBot credentials that are intentionally represented as
# placeholders in the private instance configuration. Keep the real values in
# the host-only secret file; never commit them to either repository.
apply_runtime_secrets() {
  [[ -f "$SECRET_FILE" ]] || return 0

  # shellcheck disable=SC1090
  set -a
  source "$SECRET_FILE"
  set +a

  export SECRET_WS_REVERSE_TOKEN="${SECRET_WS_REVERSE_TOKEN:-}"
  export SECRET_ACCESSTOKEN="${SECRET_ACCESSTOKEN:-}"

  python3 - \
    "$RUNTIME_DIR/astrobot/data/cmd_config.json" \
    "$RUNTIME_DIR/snowluma/data/config" <<'PY'
import os
import sys
from pathlib import Path

replacements = {
    "${SECRET_WS_REVERSE_TOKEN}": os.environ.get("SECRET_WS_REVERSE_TOKEN", ""),
    "${SECRET_ACCESSTOKEN}": os.environ.get("SECRET_ACCESSTOKEN", ""),
}

paths = [Path(sys.argv[1])]
config_dir = Path(sys.argv[2])
if config_dir.is_dir():
    paths.extend(sorted(config_dir.glob("onebot*.json")))

for raw_path in paths:
    path = Path(raw_path)
    if not path.is_file():
        continue
    text = path.read_text(encoding="utf-8-sig")
    updated = text
    for placeholder, value in replacements.items():
        if value:
            updated = updated.replace(placeholder, value)
    if updated != text:
        path.write_text(updated, encoding="utf-8")
PY

  log 'runtime OneBot secret placeholders resolved from host secret file'
}

apply_runtime_secrets

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
log "runtime will use the official latest AstrBot and SnowLuma container images"
