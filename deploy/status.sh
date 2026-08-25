#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/common-deploy.sh"

require_linux
require_layout
require_docker

target=${1:-all}
case "$target" in
  all) containers=(astrbot snowluma) ;;
  astrbot) containers=(astrbot) ;;
  snowluma) containers=(snowluma) ;;
  *) die "usage: status.sh [all|astrbot|snowluma]" ;;
esac

printf '%-12s %-10s %-10s %s\n' NAME STATE HEALTH RESTARTS
for container in "${containers[@]}"; do
  if ! docker inspect "$container" >/dev/null 2>&1; then
    printf '%-12s %-10s %-10s %s\n' "$container" absent - -
    continue
  fi
  docker inspect "$container" --format '{{.Name}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} {{.RestartCount}}' \
    | sed 's#^/##' \
    | awk '{printf "%-12s %-10s %-10s %s\n", $1, $2, $3, $4}'
done

if [[ "$target" == all || "$target" == astrbot ]]; then
  (echo >/dev/tcp/127.0.0.1/6200) >/dev/null 2>&1 \
    && printf 'AstrBot Dashboard: listening on 127.0.0.1:6200\n' \
    || printf 'AstrBot Dashboard: not listening on 127.0.0.1:6200\n'
  (echo >/dev/tcp/127.0.0.1/8001) >/dev/null 2>&1 \
    && printf 'AstrBot OneBot: listening on 127.0.0.1:8001\n' \
    || printf 'AstrBot OneBot: not listening on 127.0.0.1:8001\n'
fi
if [[ "$target" == all || "$target" == snowluma ]]; then
  (echo >/dev/tcp/127.0.0.1/5099) >/dev/null 2>&1 \
    && printf 'SnowLuma WebUI: listening on 127.0.0.1:5099\n' \
    || printf 'SnowLuma WebUI: not listening on 127.0.0.1:5099\n'
  (echo >/dev/tcp/127.0.0.1/6081) >/dev/null 2>&1 \
    && printf 'SnowLuma noVNC: listening on 127.0.0.1:6081\n' \
    || printf 'SnowLuma noVNC: not listening on 127.0.0.1:6081\n'
fi
