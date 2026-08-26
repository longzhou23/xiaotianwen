#!/usr/bin/env bash
set -euo pipefail

# Install a user crontab entry. It does not run during repository deployment;
# an operator must explicitly invoke this script after choosing a time.
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/common-deploy.sh"

require_linux
require_layout
need crontab
need flock

ON_TIME=${1:-03:30}
[[ "$ON_TIME" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || die 'usage: install-autosync-cron.sh HH:MM'
hour=${ON_TIME%%:*}
minute=${ON_TIME##*:}
hour=$((10#$hour))
minute=$((10#$minute))

cron_file=$(mktemp)
trap 'rm -f "$cron_file"' EXIT
crontab -l 2>/dev/null | awk '
  $0 == "# BEGIN XIAOTIANWEN AUTOSYNC" { skip = 1; next }
  $0 == "# END XIAOTIANWEN AUTOSYNC" { skip = 0; next }
  skip == 0 { print }
' >"$cron_file" || true

mkdir -p "$RUNTIME_DIR/.deploy" "$RUNTIME_DIR/logs"
chmod 700 "$RUNTIME_DIR/.deploy" "$RUNTIME_DIR/logs"
{
  printf '# BEGIN XIAOTIANWEN AUTOSYNC\n'
  printf '%s %s * * * cd %q && /usr/bin/flock -n %q bash %q >> %q 2>&1\n' \
    "$minute" "$hour" "$PUBLIC_DIR" \
    "$RUNTIME_DIR/.deploy/autosync.cron.lock" \
    "$PUBLIC_DIR/deploy/autosync.sh" \
    "$RUNTIME_DIR/logs/autosync.log"
  printf '# END XIAOTIANWEN AUTOSYNC\n'
} >>"$cron_file"

crontab "$cron_file"
log "installed daily autosync at $(printf '%02d:%02d' "$hour" "$minute") (host timezone; expected Asia/Shanghai)"
log 'verify with: crontab -l and tail -f runtime/logs/autosync.log'
