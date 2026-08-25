#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/common-deploy.sh"

require_linux
require_layout
require_docker
need curl
acquire_deploy_lock
require_free_space "$PROJECT_ROOT" "$MIN_FREE_MIB"

# Normal restart scripts do not pull images. This deployment/update command
# deliberately does, because AstrBot and SnowLuma are configured to follow
# their upstream latest images.
load_image_overrides

export ASTRBOT_IMAGE=${ASTRBOT_IMAGE:-soulter/astrbot:latest}
export SNOWLUMA_IMAGE=${SNOWLUMA_IMAGE:-motricseven7/snowluma:latest}
export ASTRBOT_DATA_DIR=${ASTRBOT_DATA_DIR:-$RUNTIME_DIR/astrobot/data}
export SNOWLUMA_DATA_DIR=${SNOWLUMA_DATA_DIR:-$RUNTIME_DIR/snowluma/data}
export QQ_CONFIG_DIR=${QQ_CONFIG_DIR:-$RUNTIME_DIR/snowluma/qq-config}
export QQ_DATA_DIR=${QQ_DATA_DIR:-$RUNTIME_DIR/snowluma/qq-data}
compose_env=$(write_compose_env)

astrbot_compose="$SCRIPT_DIR/astrbot/compose.yml"
snowluma_compose="$SCRIPT_DIR/snowluma-live/compose.yml"
had_live_services=0
if container_running astrbot || container_running snowluma \
  || [[ -f "$RUNTIME_DIR/astrobot/data/cmd_config.json" ]] \
  || [[ -f "$RUNTIME_DIR/astrobot/data/data_v4.db" ]]; then
  had_live_services=1
fi

normalize_runtime_permissions
CHECK_DISK_SPACE=0 "$SCRIPT_DIR/preflight.sh"

previous_astrbot_id=$(docker image inspect --format '{{.Id}}' "$ASTRBOT_IMAGE" 2>/dev/null || true)
previous_snowluma_id=$(docker image inspect --format '{{.Id}}' "$SNOWLUMA_IMAGE" 2>/dev/null || true)
services_stopped=0
rollback_handled=0

recover_stopped_services() {
  local exit_code=$?
  trap - EXIT
  if [[ "$services_stopped" == 1 && "$rollback_handled" == 0 ]]; then
    log 'deployment interrupted after services stopped; attempting to restore previous images and containers'
    [[ -n "$previous_astrbot_id" ]] && docker image tag "$previous_astrbot_id" "$ASTRBOT_IMAGE" || true
    [[ -n "$previous_snowluma_id" ]] && docker image tag "$previous_snowluma_id" "$SNOWLUMA_IMAGE" || true
    docker compose --env-file "$compose_env" -p xiaotianwen-astrbot -f "$astrbot_compose" up -d --remove-orphans || true
    docker compose --env-file "$compose_env" -p xiaotianwen-snowluma -f "$snowluma_compose" up -d --remove-orphans || true
  fi
  exit "$exit_code"
}
trap recover_stopped_services EXIT

{
  printf 'ASTRBOT_IMAGE=%s\n' "$ASTRBOT_IMAGE"
  printf 'ASTRBOT_IMAGE_ID=%s\n' "$previous_astrbot_id"
  printf 'SNOWLUMA_IMAGE=%s\n' "$SNOWLUMA_IMAGE"
  printf 'SNOWLUMA_IMAGE_ID=%s\n' "$previous_snowluma_id"
} >"$DEPLOY_STATE_DIR/previous-images.env"
chmod 600 "$DEPLOY_STATE_DIR/previous-images.env"

pull_with_retry() {
  local name=$1 compose_file=$2 attempt
  for attempt in 1 2 3; do
    log "pulling latest $name image (attempt $attempt/3)"
    if docker compose --env-file "$compose_env" -f "$compose_file" pull; then
      return 0
    fi
    sleep $((attempt * 2))
  done
  return 1
}

pull_with_retry AstrBot "$astrbot_compose" || die "failed to pull AstrBot image: $ASTRBOT_IMAGE"
pull_with_retry SnowLuma "$snowluma_compose" || die "failed to pull SnowLuma image: $SNOWLUMA_IMAGE"

docker compose --env-file "$compose_env" -p xiaotianwen-snowluma -f "$snowluma_compose" stop --timeout 30 || true
docker compose --env-file "$compose_env" -p xiaotianwen-astrbot -f "$astrbot_compose" stop --timeout 30 || true
if container_running astrbot || container_running snowluma; then
  die 'containers are still running after stop; refusing to snapshot or modify runtime'
fi
services_stopped=1

if [[ "${CREATE_PREUPDATE_BACKUP:-1}" == 1 && "$had_live_services" == 1 ]]; then
  "$SCRIPT_DIR/snapshot-runtime.sh"
fi

RESTORE_INSTANCE=${RESTORE_INSTANCE:-auto} "$SCRIPT_DIR/install.sh"
CHECK_DISK_SPACE=0 "$SCRIPT_DIR/preflight.sh"

deploy_started_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
docker compose --env-file "$compose_env" -p xiaotianwen-astrbot -f "$astrbot_compose" up -d --remove-orphans
docker compose --env-file "$compose_env" -p xiaotianwen-snowluma -f "$snowluma_compose" up -d --remove-orphans

if ! DEPLOY_STARTED_AT="$deploy_started_at" "$SCRIPT_DIR/verify.sh"; then
  log 'new deployment failed verification; attempting image rollback'
  [[ -n "$previous_astrbot_id" ]] && docker image tag "$previous_astrbot_id" "$ASTRBOT_IMAGE"
  [[ -n "$previous_snowluma_id" ]] && docker image tag "$previous_snowluma_id" "$SNOWLUMA_IMAGE"
  docker compose --env-file "$compose_env" -p xiaotianwen-astrbot -f "$astrbot_compose" up -d --remove-orphans || true
  docker compose --env-file "$compose_env" -p xiaotianwen-snowluma -f "$snowluma_compose" up -d --remove-orphans || true
  if VERIFY_TIMEOUT=90 DEPLOY_STARTED_AT="$deploy_started_at" "$SCRIPT_DIR/verify.sh"; then
    log 'previous container images restarted and passed verification'
  else
    log 'warning: previous images were restored but the restarted services did not pass verification'
  fi
  rollback_handled=1
  log "runtime snapshot retained at: $(cat "$DEPLOY_STATE_DIR/last-preupdate-backup" 2>/dev/null || printf unavailable)"
  die 'deployment verification failed; previous image IDs were restored where available'
fi

services_stopped=0
trap - EXIT
exec "$SCRIPT_DIR/record-image-digests.sh"
