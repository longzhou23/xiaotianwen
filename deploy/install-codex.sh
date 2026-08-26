#!/usr/bin/env bash
set -euo pipefail

# Install Codex CLI into the persistent AstrBot data mount.  This script is
# intentionally executed after the AstrBot container is started: the official
# image supplies node/npm, while the installation target survives recreation.
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/common-deploy.sh"

require_linux
require_layout
require_docker
need awk

CODEX_PACKAGE=${CODEX_PACKAGE:-@openai/codex}
CODEX_VERSION=${CODEX_VERSION:-latest}
CODEX_INSTALL_PREFIX=${CODEX_INSTALL_PREFIX:-/AstrBot/data/codex}
CODEX_INSTALL_REQUIRED=${CODEX_INSTALL_REQUIRED:-1}
CODEX_FORCE_INSTALL=${CODEX_FORCE_INSTALL:-0}

case "$CODEX_INSTALL_REQUIRED" in
  0|1) ;;
  *) die 'CODEX_INSTALL_REQUIRED must be 0 or 1' ;;
esac
case "$CODEX_FORCE_INSTALL" in
  0|1) ;;
  *) die 'CODEX_FORCE_INSTALL must be 0 or 1' ;;
esac
[[ "$CODEX_PACKAGE" =~ ^@?[A-Za-z0-9._/-]+$ ]] || die "invalid CODEX_PACKAGE: $CODEX_PACKAGE"
codex_version_pattern='^[A-Za-z0-9._*^~<>=+-]+$'
[[ "$CODEX_VERSION" =~ $codex_version_pattern ]] || die "invalid CODEX_VERSION"

if ! container_running astrbot; then
  if [[ "$CODEX_INSTALL_REQUIRED" == 1 ]]; then
    die 'astrbot container must be running before installing Codex CLI'
  fi
  log 'astrbot is not running; Codex installation skipped (CODEX_INSTALL_REQUIRED=0)'
  exit 0
fi

run_in_astrbot() {
  docker exec -u 0 astrbot "$@"
}

if ! run_in_astrbot sh -lc 'command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1'; then
  if [[ "$CODEX_INSTALL_REQUIRED" == 1 ]]; then
    die 'AstrBot image does not provide node/npm; cannot install Codex CLI'
  fi
  log 'AstrBot image does not provide node/npm; Codex installation skipped (CODEX_INSTALL_REQUIRED=0)'
  exit 0
fi

node_version=$(run_in_astrbot node --version | tr -d '\r\n')
npm_version=$(run_in_astrbot npm --version | tr -d '\r\n')
[[ "$node_version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+ ]] || die "unexpected node version: $node_version"
[[ "$npm_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+ ]] || die "unexpected npm version: $npm_version"
log "Codex install runtime: node=$node_version npm=$npm_version"

codex_bin="$CODEX_INSTALL_PREFIX/bin/codex"
if [[ "$CODEX_FORCE_INSTALL" != 1 ]] && run_in_astrbot test -x "$codex_bin"; then
  if installed_version=$(run_in_astrbot "$codex_bin" --version 2>/dev/null | head -n 1); then
    [[ -n "$installed_version" ]] || installed_version='version command returned no text'
    log "Codex CLI already available: $installed_version"
    exit 0
  fi
  log 'existing Codex executable failed its version check; reinstalling'
fi

package_spec="$CODEX_PACKAGE@$CODEX_VERSION"
log "installing Codex CLI package into $CODEX_INSTALL_PREFIX"
run_in_astrbot npm install \
  --global \
  --prefix "$CODEX_INSTALL_PREFIX" \
  --no-audit \
  --no-fund \
  "$package_spec"

run_in_astrbot test -x "$codex_bin" || die "Codex executable was not created: $codex_bin"
installed_version=$(run_in_astrbot "$codex_bin" --version | head -n 1)
[[ -n "$installed_version" ]] || die 'Codex version check returned no output'
log "Codex CLI installed successfully: $installed_version"
log 'Codex authentication is intentionally not performed by deployment; use CODEX_HOME inside the persistent mount or a separate operator login step'
