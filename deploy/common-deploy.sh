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

# A user-owned fallback keeps a non-root deployment usable while keeping
# credentials outside both Git repositories. The conventional /etc path still
# wins whenever it exists.
if [[ "$SECRET_FILE" == /etc/xiaotianwen/secrets.env && ! -f "$SECRET_FILE" ]]; then
  host_secret_fallback="$PROJECT_ROOT/.host-secrets/secrets.env"
  [[ -f "$host_secret_fallback" ]] && SECRET_FILE="$host_secret_fallback"
fi

umask 077

log() { printf '%s [deploy] %s\n' "$(date '+%F %T')" "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"; }

require_linux() {
  [[ "$(uname -s)" == Linux ]] || die 'these deployment scripts must run on Linux'
}

require_layout() {
  [[ -d "$PUBLIC_DIR" ]] || die "public repository not found: $PUBLIC_DIR"
  [[ -d "$PRIVATE_DIR" ]] || die "private repository not found: $PRIVATE_DIR"
}

require_secret_file() {
  [[ -f "$SECRET_FILE" ]] || die "secret file not found: $SECRET_FILE"
  [[ "$(stat -c '%a' "$SECRET_FILE" 2>/dev/null || true)" == 600 ]] || die "secret file must have mode 600: $SECRET_FILE"
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
