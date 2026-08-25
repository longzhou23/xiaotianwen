#!/usr/bin/env bash
# Local helper: build the SnowLuma Docker image for one or more platforms.
#
# Examples:
#   ./scripts/build-image.sh                          # load amd64 locally
#   SNOWLUMA_TAG=v1.6.35 ./scripts/build-image.sh     # auto-download release
#   PUSH=1 PLATFORM=linux/arm64 SNOWLUMA_TAG=v1.6.35 ./scripts/build-image.sh
#
# Tooling: requires Docker buildx and (when SNOWLUMA_TAG is set) the gh CLI.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRAMEWORK_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

IMAGE="${IMAGE:-snowluma-docker-framework:latest}"
PUSH="${PUSH:-0}"
SNOWLUMA_REPO="${SNOWLUMA_REPO:-SnowLuma/SnowLuma}"
SNOWLUMA_TAG="${SNOWLUMA_TAG:-}"
ARTIFACT="${FRAMEWORK_DIR}/SnowLuma.Framework.tar.gz"

if [ "${PUSH}" = "1" ] || [ "${PUSH}" = "true" ]; then
  PLATFORM="${PLATFORM:-linux/amd64}"
  OUTPUT="${OUTPUT:---push}"
else
  PLATFORM="${PLATFORM:-linux/amd64}"
  OUTPUT="${OUTPUT:---load}"
fi

# This local helper only supports single-platform builds — multi-arch
# manifest creation is what CI is for. Users wanting multi-arch should
# push a SnowLuma tag and let .github/workflows/docker-image.yml handle it.
case ",${PLATFORM}," in
  *,*,*) echo "PLATFORM must be a single value (linux/amd64 or linux/arm64); use CI for multi-arch." >&2; exit 1 ;;
esac

case "${PLATFORM}" in
  linux/amd64) asset_arch="linux-x64" ;;
  linux/arm64) asset_arch="linux-arm64" ;;
  *) echo "Unsupported PLATFORM: ${PLATFORM}" >&2; exit 1 ;;
esac

if [ -n "${SNOWLUMA_TAG}" ]; then
  if ! command -v gh >/dev/null 2>&1; then
    echo "gh CLI not found. Install from https://cli.github.com/ then re-run." >&2
    exit 1
  fi
  asset="SnowLuma-${SNOWLUMA_TAG}-${asset_arch}-lite.tar.gz"
  echo "Fetching ${asset} from ${SNOWLUMA_REPO}@${SNOWLUMA_TAG}"
  gh release download "${SNOWLUMA_TAG}" \
    --repo "${SNOWLUMA_REPO}" \
    --pattern "${asset}" \
    --output "${ARTIFACT}" \
    --clobber
elif [ ! -f "${ARTIFACT}" ]; then
  echo "Missing ${ARTIFACT}." >&2
  echo "Either set SNOWLUMA_TAG=vX.Y.Z to auto-download, or place SnowLuma.Framework.tar.gz at the repo root." >&2
  exit 1
fi

docker buildx build \
  --platform "${PLATFORM}" \
  --tag "${IMAGE}" \
  --file "${FRAMEWORK_DIR}/Dockerfile" \
  ${OUTPUT} \
  "${FRAMEWORK_DIR}"

echo "Built ${IMAGE} for ${PLATFORM}"
