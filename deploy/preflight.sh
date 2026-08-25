#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/common-deploy.sh"

require_linux
require_layout
require_docker
need python3
need rsync
need curl
require_secret_file

if [[ "${CHECK_DISK_SPACE:-1}" == 1 ]]; then
  require_free_space "$PROJECT_ROOT" "$MIN_FREE_MIB"
fi

python3 - "$SECRET_FILE" "$PRIVATE_DIR" "$RUNTIME_DIR" <<'PY'
import json
import re
import sys
from pathlib import Path

secret_path = Path(sys.argv[1])
private_dir = Path(sys.argv[2])
runtime_dir = Path(sys.argv[3])
values = {}
for number, raw_line in enumerate(secret_path.read_text(encoding="utf-8-sig").splitlines(), 1):
    line = raw_line.strip()
    if not line or line.startswith("#"):
        continue
    if "=" not in line:
        raise SystemExit(f"invalid secret line {number}: expected NAME=value")
    name, value = line.split("=", 1)
    name = name.strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise SystemExit(f"invalid secret variable name on line {number}")
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    values[name] = value

left = values.get("SECRET_WS_REVERSE_TOKEN", "")
right = values.get("SECRET_ACCESSTOKEN", "")
if not left or not right:
    raise SystemExit("OneBot secret pair is missing")
if left != right:
    raise SystemExit("OneBot secret pair does not match")

json_roots = [
    private_dir / "instance/astrobot-data",
    private_dir / "instance/snowluma-data/data/config",
]
for root in json_roots:
    if not root.exists():
        continue
    for path in root.rglob("*.json"):
        try:
            json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            raise SystemExit(f"invalid JSON: {path}: {exc}") from exc

required_runtime_files = [
    runtime_dir / "astrobot/data/cmd_config.json",
    runtime_dir / "snowluma/data/config/onebot.json",
]
for path in required_runtime_files:
    if path.exists():
        json.loads(path.read_text(encoding="utf-8-sig"))
PY

load_image_overrides
export ASTRBOT_IMAGE=${ASTROBOT_IMAGE:-soulter/astrbot:latest}
export SNOWLUMA_IMAGE=${SNOWLUMA_IMAGE:-motricseven7/snowluma:latest}
export ASTRBOT_DATA_DIR=${ASTROBOT_DATA_DIR:-$RUNTIME_DIR/astrobot/data}
export SNOWLUMA_DATA_DIR=${SNOWLUMA_DATA_DIR:-$RUNTIME_DIR/snowluma/data}
export QQ_CONFIG_DIR=${QQ_CONFIG_DIR:-$RUNTIME_DIR/snowluma/qq-config}
export QQ_DATA_DIR=${QQ_DATA_DIR:-$RUNTIME_DIR/snowluma/qq-data}
compose_env=$(write_compose_env)

docker compose --env-file "$compose_env" -p xiaotianwen-astrbot -f "$SCRIPT_DIR/astrbot/compose.yml" config --quiet
docker compose --env-file "$compose_env" -p xiaotianwen-snowluma -f "$SCRIPT_DIR/snowluma-live/compose.yml" config --quiet

log 'deployment preflight passed'
