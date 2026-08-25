#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/common-deploy.sh"

need docker
docker info >/dev/null 2>&1 || die 'Docker daemon is unavailable'

ASTRBOT_IMAGE=${ASTRBOT_IMAGE:-soulter/astrbot:latest}
SNOWLUMA_IMAGE=${SNOWLUMA_IMAGE:-motricseven7/snowluma:latest}
OUTPUT_FILE=${IMAGE_RECORD_FILE:-$RUNTIME_DIR/deployed-images.env}
load_image_overrides

ASTRBOT_IMAGE=${ASTRBOT_IMAGE:-soulter/astrbot:latest}
SNOWLUMA_IMAGE=${SNOWLUMA_IMAGE:-motricseven7/snowluma:latest}

image_id() {
  docker image inspect --format '{{.Id}}' "$1"
}

image_digest() {
  docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$1" | head -n 1
}

astrbot_id=$(image_id "$ASTRBOT_IMAGE")
snowluma_id=$(image_id "$SNOWLUMA_IMAGE")
astrbot_digest=$(image_digest "$ASTRBOT_IMAGE")
snowluma_digest=$(image_digest "$SNOWLUMA_IMAGE")

umask 077
mkdir -p "$(dirname -- "$OUTPUT_FILE")"
cat >"$OUTPUT_FILE" <<EOF
# Generated after a successful xiaotianwen deployment.
# This is audit and rollback metadata, not a secret.
DEPLOYED_AT=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
ASTRBOT_IMAGE=${ASTRBOT_IMAGE}
ASTRBOT_IMAGE_ID=${astrbot_id}
ASTRBOT_IMAGE_DIGEST=${astrbot_digest}
SNOWLUMA_IMAGE=${SNOWLUMA_IMAGE}
SNOWLUMA_IMAGE_ID=${snowluma_id}
SNOWLUMA_IMAGE_DIGEST=${snowluma_digest}
EOF
chmod 600 "$OUTPUT_FILE"
log "deployed image metadata written to $OUTPUT_FILE"
