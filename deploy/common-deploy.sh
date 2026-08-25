#!/usr/bin/env bash
set -euo pipefail

DEPLOY_SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PUBLIC_DIR=$(cd -- "$DEPLOY_SCRIPT_DIR/.." && pwd)
PROJECT_ROOT=${PROJECT_ROOT:-$(cd -- "$PUBLIC_DIR/.." && pwd)}
PRIVATE_DIR=${PRIVATE_DIR:-$PROJECT_ROOT/private}
RUNTIME_DIR=${RUNTIME_DIR:-$PROJECT_ROOT/runtime}
SECRET_FILE=${SECRET_FILE:-/etc/xiaotianwen/secrets.env}
BACKUP_ROOT=${BACKUP_ROOT:-$PROJECT_ROOT/backups}
IMAGE_CONFIG_FILE=${IMAGE_CONFIG_FILE:-$PROJECT_ROOT/host-images.env}
DEPLOY_STATE_DIR=${DEPLOY_STATE_DIR:-$RUNTIME_DIR/.deploy}
INSTANCE_MARKER=${INSTANCE_MARKER:-$DEPLOY_STATE_DIR/instance-restored}
DEPLOY_LOCK=${DEPLOY_LOCK:-$DEPLOY_STATE_DIR/deploy.lock}
MIN_FREE_MIB=${MIN_FREE_MIB:-4096}

# A user-owned fallback keeps a non-root deployment usable while keeping
# credentials outside both Git repositories. The conventional /etc path still
# wins whenever it exists.
if [[ "$SECRET_FILE" == /etc/xiaotianwen/secrets.env && ! -e "$SECRET_FILE" ]]; then
  host_secret_fallback="$PROJECT_ROOT/.host-secrets/secrets.env"
  SECRET_FILE="$host_secret_fallback"
fi

umask 077

log() { printf '%s [deploy] %s\n' "$(date '+%F %T')" "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"; }

is_non_negative_integer() { [[ "${1:-}" =~ ^[0-9]+$ ]]; }

acquire_deploy_lock() {
  need flock
  mkdir -p "$DEPLOY_STATE_DIR"
  exec {DEPLOY_LOCK_FD}>"$DEPLOY_LOCK"
  flock -n "$DEPLOY_LOCK_FD" || die "another deployment command is running: $DEPLOY_LOCK"
}

require_linux() {
  [[ "$(uname -s)" == Linux ]] || die 'these deployment scripts must run on Linux'
}

require_layout() {
  [[ -d "$PUBLIC_DIR" ]] || die "public repository not found: $PUBLIC_DIR"
  [[ -d "$PRIVATE_DIR" ]] || die "private repository not found: $PRIVATE_DIR"
}

require_secret_file() {
  [[ -f "$SECRET_FILE" ]] || die "secret file not found: $SECRET_FILE"
  [[ -r "$SECRET_FILE" ]] || die "secret file is not readable by $(id -un): $SECRET_FILE"
  [[ "$(stat -c '%a' "$SECRET_FILE" 2>/dev/null || true)" == 600 ]] || die "secret file must have mode 600: $SECRET_FILE"
}

ensure_onebot_secret_pair() {
  need python3
  mkdir -p "$(dirname -- "$SECRET_FILE")"
  [[ -e "$SECRET_FILE" ]] || : >"$SECRET_FILE"
  chmod 600 "$SECRET_FILE"

  python3 - "$SECRET_FILE" <<'PY'
import re
import secrets
import sys
from pathlib import Path

path = Path(sys.argv[1])
lines = path.read_text(encoding="utf-8").splitlines()
values = {}
for line in lines:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        continue
    key, value = stripped.split("=", 1)
    key = key.strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        values[key] = value.strip().strip("'\"")

astrbot = values.get("SECRET_WS_REVERSE_TOKEN", "")
snowluma = values.get("SECRET_ACCESSTOKEN", "")
if astrbot and snowluma and astrbot != snowluma:
    raise SystemExit("OneBot secret mismatch: SECRET_WS_REVERSE_TOKEN and SECRET_ACCESSTOKEN must be identical")

token = astrbot or snowluma or secrets.token_hex(32)
missing = []
if not astrbot:
    missing.append(f"SECRET_WS_REVERSE_TOKEN={token}")
if not snowluma:
    missing.append(f"SECRET_ACCESSTOKEN={token}")
if missing:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        if lines and lines[-1] != "":
            handle.write("\n")
        handle.write("\n".join(missing) + "\n")
PY

  chmod 600 "$SECRET_FILE"
  log "OneBot secret pair is present in host-only secret file"
}

require_docker() {
  need docker
  docker info >/dev/null 2>&1 || die 'Docker daemon is unavailable or the current user cannot access it'
  docker compose version >/dev/null 2>&1 || die 'Docker Compose v2 is unavailable'
}

require_free_space() {
  local target=${1:-$PROJECT_ROOT}
  local minimum=${2:-$MIN_FREE_MIB}
  is_non_negative_integer "$minimum" || die "MIN_FREE_MIB must be a non-negative integer: $minimum"
  [[ "$minimum" == 0 ]] && return 0
  mkdir -p "$target"
  local available_kib available_mib
  available_kib=$(df -Pk "$target" | awk 'NR == 2 {print $4}')
  [[ "$available_kib" =~ ^[0-9]+$ ]] || die "cannot determine free space for $target"
  available_mib=$((available_kib / 1024))
  ((available_mib >= minimum)) || die "only ${available_mib} MiB free under $target; at least ${minimum} MiB is required"
}

container_running() {
  [[ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null || true)" == true ]]
}

normalize_runtime_permissions() {
  local uid gid
  uid=$(id -u)
  gid=$(id -g)

  # AstrBot runs as root in the upstream image and may leave these deployment-
  # managed paths root-owned. Repair only code/config paths; databases and user
  # data are deliberately left untouched.
  if docker inspect astrbot >/dev/null 2>&1; then
    docker exec astrbot sh -lc \
      "chown -R $uid:$gid /AstrBot/data/plugins /AstrBot/data/config 2>/dev/null || true; chown $uid:$gid /AstrBot/data/cmd_config.json 2>/dev/null || true" \
      >/dev/null
  fi
}

write_compose_env() {
  local output=$DEPLOY_STATE_DIR/compose.env
  local v_image_a=${ASTRBOT_IMAGE:-soulter/astrbot:latest}
  local v_image_s=${SNOWLUMA_IMAGE:-motricseven7/snowluma:latest}
  local v_data_a=${ASTROBOT_DATA_DIR:-$RUNTIME_DIR/astrobot/data}
  local v_data_s=${SNOWLUMA_DATA_DIR:-$RUNTIME_DIR/snowluma/data}
  local v_qq_config=${QQ_CONFIG_DIR:-$RUNTIME_DIR/snowluma/qq-config}
  local v_qq_data=${QQ_DATA_DIR:-$RUNTIME_DIR/snowluma/qq-data}
  mkdir -p "$DEPLOY_STATE_DIR"
  umask 077
  {
    printf 'ASTRBOT_IMAGE=%s\n' "$v_image_a"
    printf 'SNOWLUMA_IMAGE=%s\n' "$v_image_s"
    printf 'ASTROBOT_DATA_DIR=%s\n' "$v_data_a"
    printf 'SNOWLUMA_DATA_DIR=%s\n' "$v_data_s"
    printf 'QQ_CONFIG_DIR=%s\n' "$v_qq_config"
    printf 'QQ_DATA_DIR=%s\n' "$v_qq_data"
    printf 'ASTROBOT_BIND_HOST=%s\n' "${ASTROBOT_BIND_HOST:-0.0.0.0}"
    printf 'SNOWLUMA_BIND_HOST=%s\n' "${SNOWLUMA_BIND_HOST:-0.0.0.0}"
    printf 'SNOWLUMA_UID=%s\n' "${SNOWLUMA_UID:-$(id -u)}"
    printf 'SNOWLUMA_GID=%s\n' "${SNOWLUMA_GID:-$(id -g)}"
    printf 'ASTROBOT_DASHBOARD_PORT=%s\n' "${ASTROBOT_DASHBOARD_PORT:-6200}"
    printf 'ASTROBOT_ONEBOT_PORT=%s\n' "${ASTROBOT_ONEBOT_PORT:-8001}"
  } >"$output"
  chmod 600 "$output"
  printf '%s\n' "$output"
  return 0
}

copy_tree() {
  local source=$1 target=$2
  mkdir -p "$target"
  cp -a "$source"/. "$target"/
}

load_image_overrides() {
  [[ -f "$IMAGE_CONFIG_FILE" ]] || return 0
  local key value
  while IFS='=' read -r key value || [[ -n "$key" ]]; do
    key=${key//[[:space:]]/}
    [[ -z "$key" || "$key" == \#* ]] && continue
    case "$key" in
      ASTRBOT_IMAGE|SNOWLUMA_IMAGE)
        [[ "$value" =~ ^[A-Za-z0-9._/:@-]+$ ]] || die "invalid image value in $IMAGE_CONFIG_FILE: $key"
        export "$key=$value"
        ;;
      *)
        die "unsupported key in $IMAGE_CONFIG_FILE: $key"
        ;;
    esac
  done <"$IMAGE_CONFIG_FILE"
  log "loaded container image overrides from $IMAGE_CONFIG_FILE"
}

clone_with_optional_token() {
  local url=$1 target=$2 token=${GITHUB_TOKEN:-}
  [[ -e "$target" ]] && return 0
  mkdir -p "$(dirname -- "$target")"
  if [[ -z "$token" ]]; then
    git clone --depth=1 "$url" "$target"
    return
  fi
  local askpass_dir askpass
  askpass_dir=$(mktemp -d)
  askpass="$askpass_dir/askpass.sh"
  cat >"$askpass" <<'ASKPASS'
#!/usr/bin/env bash
case "${1:-}" in
  *Username*) printf '%s\n' "${GITHUB_USERNAME:-x-access-token}" ;;
  *) printf '%s\n' "${GITHUB_TOKEN}" ;;
esac
ASKPASS
  chmod 700 "$askpass"
  GIT_ASKPASS="$askpass" GIT_TERMINAL_PROMPT=0 git clone --depth=1 "$url" "$target"
  rm -rf "$askpass_dir"
}
