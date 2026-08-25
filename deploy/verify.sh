#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/common-deploy.sh"

require_linux
require_docker
need curl

VERIFY_TIMEOUT=${VERIFY_TIMEOUT:-120}
is_non_negative_integer "$VERIFY_TIMEOUT" || die "VERIFY_TIMEOUT must be a non-negative integer"
started_at=${DEPLOY_STARTED_AT:-10m}

wait_http() {
  local url=$1 name=$2 deadline=$((SECONDS + VERIFY_TIMEOUT))
  while ((SECONDS <= deadline)); do
    if curl --fail --silent --show-error --max-time 3 --output /dev/null "$url" 2>/dev/null; then
      log "$name is healthy: $url"
      return 0
    fi
    sleep 1
  done
  return 1
}

wait_tcp() {
  local port=$1 name=$2 deadline=$((SECONDS + VERIFY_TIMEOUT))
  while ((SECONDS <= deadline)); do
    if (echo >/dev/tcp/127.0.0.1/"$port") >/dev/null 2>&1; then
      log "$name is listening on 127.0.0.1:$port"
      return 0
    fi
    sleep 1
  done
  return 1
}

container_running astrbot || die 'AstrBot container is not running'
container_running snowluma || die 'SnowLuma container is not running'
wait_http 'http://127.0.0.1:6200/' 'AstrBot Dashboard' || die 'AstrBot Dashboard health check failed'
wait_tcp 8001 'AstrBot OneBot endpoint' || die 'AstrBot OneBot health check failed'
wait_http 'http://127.0.0.1:5099/' 'SnowLuma WebUI' || die 'SnowLuma WebUI health check failed'
wait_tcp 6081 'SnowLuma noVNC' || die 'SnowLuma noVNC health check failed'

if docker logs --since "$started_at" astrbot 2>&1 | grep -Eq 'GET /ws .* 403|WebSocket handshake failed: Unexpected response status 403'; then
  die 'OneBot authentication failed with HTTP 403 after deployment'
fi
if docker logs --since "$started_at" snowluma 2>&1 | grep -Eq 'WebSocket handshake failed: Unexpected response status 403'; then
  die 'SnowLuma OneBot client received HTTP 403 after deployment'
fi

for container in astrbot snowluma; do
  state=$(docker inspect "$container" --format '{{.State.Status}} restarts={{.RestartCount}}')
  log "$container state: $state"
done

log 'deployment verification passed'
