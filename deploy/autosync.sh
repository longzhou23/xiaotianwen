#!/usr/bin/env bash
set -euo pipefail

# Synchronise approved live-instance data into the private repository and
# publish repository changes. This script never stages secrets or QQ login
# data by default. It is intended to be called by cron/systemd on Linux.
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/common-deploy.sh"

require_linux
require_layout
need git
need rsync
need flock

AUTOSYNC_DRY_RUN=${AUTOSYNC_DRY_RUN:-0}
AUTOSYNC_PUSH=${AUTOSYNC_PUSH:-1}
AUTOSYNC_QUIESCE=${AUTOSYNC_QUIESCE:-1}
AUTOSYNC_INCLUDE_QQ_DATA=${AUTOSYNC_INCLUDE_QQ_DATA:-0}

for value_name in AUTOSYNC_DRY_RUN AUTOSYNC_PUSH AUTOSYNC_QUIESCE AUTOSYNC_INCLUDE_QQ_DATA; do
  value=${!value_name}
  [[ "$value" == 0 || "$value" == 1 ]] || die "$value_name must be 0 or 1"
done

acquire_deploy_lock
require_free_space "$PROJECT_ROOT" 0

log "nightly sync started (dry_run=$AUTOSYNC_DRY_RUN push=$AUTOSYNC_PUSH quiesce=$AUTOSYNC_QUIESCE)"

for repo in "$PUBLIC_DIR" "$PRIVATE_DIR"; do
  git -C "$repo" rev-parse --show-toplevel >/dev/null || die "not a Git repository: $repo"
done

# Do not overwrite an operator's uncommitted private changes.
[[ -z "$(git -C "$PRIVATE_DIR" status --porcelain)" ]] || die "private repository is dirty; refusing automatic sync: $PRIVATE_DIR"

sync_tree() {
  local source=$1 target=$2
  [[ -d "$source" ]] || { log "source does not exist, skipped: $source"; return 0; }
  if [[ "$AUTOSYNC_DRY_RUN" == 1 ]]; then
    [[ -d "$target" ]] || { log "target does not exist, would create: $target"; return 0; }
  else
    mkdir -p "$target"
  fi
  local mode='-a'
  [[ "$AUTOSYNC_DRY_RUN" == 1 ]] && mode='-anv'
  rsync "$mode" --exclude='logs/' --exclude='cache/' --exclude='.cache/' \
    --exclude='temp/' --exclude='thumb_cache/' --exclude='cached_images/' \
    --exclude='image_cache/' --exclude='__pycache__/' \
    --exclude='attachments/' --exclude='codex/' --exclude='CODEX_HOME/' \
    --exclude='site-packages/' --exclude='webchat/' --exclude='plugin-backups/' \
    --exclude='web_data/' --exclude='plugin_data/*/models/' \
    --exclude='plugin_data/*/tmp/' --exclude='plugin_data/*/temp/' \
    --exclude='*.pyc' \
    --exclude='*.log' --exclude='*.tmp' --exclude='*.swp' \
    --exclude='plugins/' --exclude='config/' --exclude='cmd_config.json' \
    --exclude='mcp_server.json' --exclude='skills.json' \
    --exclude='.env' --exclude='*.pem' --exclude='*.key' \
    --exclude='*.bak' --exclude='*.bak.*' --exclude='*.bak*' \
    "$source"/ "$target"/
}

sync_approved_instance_data() {
  sync_tree "$RUNTIME_DIR/astrobot/data" "$PRIVATE_DIR/instance/astrobot-data"
  sync_tree "$RUNTIME_DIR/snowluma/data" "$PRIVATE_DIR/instance/snowluma-data/data"

  # QQ config/login data may contain refresh tokens, cookies and device state.
  if [[ "$AUTOSYNC_INCLUDE_QQ_DATA" == 1 ]]; then
    log 'including QQ config/data because AUTOSYNC_INCLUDE_QQ_DATA=1; ensure the private remote is access-controlled'
    sync_tree "$RUNTIME_DIR/snowluma/qq-config" "$PRIVATE_DIR/instance/qq-config"
    sync_tree "$RUNTIME_DIR/snowluma/qq-data" "$PRIVATE_DIR/instance/qq-data"
  else
    log 'QQ config/data excluded from cloud sync (set AUTOSYNC_INCLUDE_QQ_DATA=1 only deliberately)'
  fi
}

if [[ "$AUTOSYNC_DRY_RUN" == 1 ]]; then
  sync_approved_instance_data
  log 'dry-run requested: no services stopped, commits or pushes performed'
  exit 0
fi

services_quiesced=0
restart_after_sync() {
  if [[ "$services_quiesced" == 1 ]]; then
    log 'restarting services after automatic sync'
    # autosync already owns DEPLOY_LOCK; calling start.sh/stop.sh here would
    # deadlock because those entry points acquire the same non-reentrant lock.
    docker start astrbot snowluma >/dev/null \
      || log 'warning: services did not restart cleanly; inspect deploy/status.sh'
  fi
}
trap restart_after_sync EXIT

if [[ "$AUTOSYNC_QUIESCE" == 1 ]]; then
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    log 'stopping AstrBot and SnowLuma for a consistent file/database snapshot'
    services_quiesced=1
    docker stop astrbot snowluma >/dev/null
  else
    die 'Docker is unavailable; set AUTOSYNC_QUIESCE=0 only if an online snapshot is acceptable'
  fi
fi

sync_approved_instance_data

secret_pattern='(sk-[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY|AWS_SECRET_ACCESS_KEY|SECRET_[A-Z0-9_]+=([^$]|$))'
scan_staged_for_secrets() {
  local repo=$1
  if git -C "$repo" diff --cached --binary | rg -n -i "$secret_pattern" >/dev/null 2>&1; then
    git -C "$repo" diff --cached --binary | rg -n -i "$secret_pattern" | head -n 5 >&2 || true
    die "possible secret detected in staged changes; automatic push aborted: $repo"
  fi
}

ensure_staged_paths_allowed() {
  local repo=$1 scope=$2 path
  while IFS= read -r -d '' path; do
    if [[ "$scope" == public ]]; then
      case "$path" in
        README.md|LICENSE|plugins/*|components/*|deploy/*|scripts/*|config/*|docs/*) ;;
        *) die "staged path is outside public autosync allowlist: $path" ;;
      esac
    else
      case "$path" in
        instance/*|knowledge/*|plugins.lock.yaml|deployment/*) ;;
        *) die "staged path is outside private autosync allowlist: $path" ;;
      esac
    fi
  done < <(git -C "$repo" diff --cached --name-only -z)
}

commit_and_push() {
  local repo=$1 message=$2
  if git -C "$repo" diff --cached --quiet; then
    log "no staged changes in $(basename "$repo")"
    return 0
  fi
  scan_staged_for_secrets "$repo"
  git -C "$repo" commit -m "$message"
  if [[ "$AUTOSYNC_PUSH" == 1 ]]; then
    git -C "$repo" push origin HEAD
    log "pushed $(basename "$repo")"
  fi
}

# Public changes are limited to code/templates/docs. Runtime data is staged
# only in the private instance repository.
git -C "$PUBLIC_DIR" add -- README.md LICENSE plugins components deploy scripts config docs
ensure_staged_paths_allowed "$PUBLIC_DIR" public
commit_and_push "$PUBLIC_DIR" "chore(auto-sync): publish public changes $(date '+%Y-%m-%d %H:%M %Z')"

git -C "$PRIVATE_DIR" add -- instance knowledge plugins.lock.yaml deployment
ensure_staged_paths_allowed "$PRIVATE_DIR" private
commit_and_push "$PRIVATE_DIR" "chore(auto-sync): snapshot instance data $(date '+%Y-%m-%d %H:%M %Z')"

log 'nightly sync completed'
