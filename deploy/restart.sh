#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

target=${1:-all}
case "$target" in
  all|astrbot|snowluma) ;;
  *) printf 'usage: restart.sh [all|astrbot|snowluma]\n' >&2; exit 2 ;;
esac

"$SCRIPT_DIR/stop.sh" "$target"
"$SCRIPT_DIR/start.sh" "$target"
"$SCRIPT_DIR/status.sh" "$target"
