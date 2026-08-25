#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/common-deploy.sh"

require_linux
require_layout
require_docker
acquire_deploy_lock

target=${1:-all}
case "$target" in
  all|astrbot|snowluma) ;;
  *) die "usage: start.sh [all|astrbot|snowluma]" ;;
esac

load_image_overrides
export ASTRBOT_IMAGE=${ASTRBOT_IMAGE:-soulter/astrbot:latest}
export SNOWLUMA_IMAGE=${SNOWLUMA_IMAGE:-motricseven7/snowluma:latest}
export ASTRBOT_DATA_DIR=${ASTRBOT_DATA_DIR:-$RUNTIME_DIR/astrobot/data}
export SNOWLUMA_DATA_DIR=${SNOWLUMA_DATA_DIR:-$RUNTIME_DIR/snowluma/data}
export QQ_CONFIG_DIR=${QQ_CONFIG_DIR:-$RUNTIME_DIR/snowluma/qq-config}
export QQ_DATA_DIR=${QQ_DATA_DIR:-$RUNTIME_DIR/snowluma/qq-data}
compose_env=$(write_compose_env)

start_astrbot() {
  docker compose --env-file "$compose_env" -p xiaotianwen-astrbot \
    -f "$SCRIPT_DIR/astrbot/compose.yml" up -d --remove-orphans
}

start_snowluma() {
  docker compose --env-file "$compose_env" -p xiaotianwen-snowluma \
    -f "$SCRIPT_DIR/snowluma-live/compose.yml" up -d --remove-orphans
}

case "$target" in
  all)
    start_astrbot
    start_snowluma
    ;;
  astrbot) start_astrbot ;;
  snowluma) start_snowluma ;;
esac

log "started: $target"
