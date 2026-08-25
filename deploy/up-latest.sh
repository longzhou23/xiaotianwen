#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/common-deploy.sh"

require_linux
require_layout
need docker
docker info >/dev/null 2>&1 || die 'Docker daemon is unavailable'
docker compose version >/dev/null 2>&1 || die 'Docker Compose v2 is unavailable'

# Normal restart scripts do not pull images. This deployment/update command
# deliberately does, because AstrBot and SnowLuma are configured to follow
# their upstream latest images.
"$SCRIPT_DIR/install.sh"
load_image_overrides

export ASTRBOT_IMAGE=${ASTRBOT_IMAGE:-soulter/astrbot:latest}
export SNOWLUMA_IMAGE=${SNOWLUMA_IMAGE:-motricseven7/snowluma:latest}
export ASTRBOT_DATA_DIR=${ASTRBOT_DATA_DIR:-$RUNTIME_DIR/astrobot/data}
export SNOWLUMA_DATA_DIR=${SNOWLUMA_DATA_DIR:-$RUNTIME_DIR/snowluma/data}
export QQ_CONFIG_DIR=${QQ_CONFIG_DIR:-$RUNTIME_DIR/snowluma/qq-config}
export QQ_DATA_DIR=${QQ_DATA_DIR:-$RUNTIME_DIR/snowluma/qq-data}

astrbot_compose="$SCRIPT_DIR/astrbot/compose.yml"
snowluma_compose="$SCRIPT_DIR/snowluma-live/compose.yml"

log "pulling latest AstrBot image: $ASTRBOT_IMAGE"
docker compose -p xiaotianwen-astrbot -f "$astrbot_compose" pull
log "pulling latest SnowLuma image: $SNOWLUMA_IMAGE"
docker compose -p xiaotianwen-snowluma -f "$snowluma_compose" pull

docker compose -p xiaotianwen-astrbot -f "$astrbot_compose" up -d --remove-orphans
docker compose -p xiaotianwen-snowluma -f "$snowluma_compose" up -d --remove-orphans

wait_tcp() {
  local port=$1 name=$2 attempt
  for attempt in $(seq 1 90); do
    if (echo >/dev/tcp/127.0.0.1/"$port") >/dev/null 2>&1; then
      log "$name is listening on 127.0.0.1:$port"
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_tcp 6200 'AstrBot Dashboard' || die 'AstrBot Dashboard did not become ready on port 6200'
wait_tcp 8001 'AstrBot OneBot endpoint' || die 'AstrBot OneBot endpoint did not become ready on port 8001'
wait_tcp 5099 'SnowLuma WebUI' || die 'SnowLuma WebUI did not become ready on port 5099'

exec "$SCRIPT_DIR/record-image-digests.sh"
