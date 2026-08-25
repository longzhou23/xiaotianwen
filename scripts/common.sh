#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
RUNTIME="$ROOT/.runtime"
RUN_DIR="$RUNTIME/run"
LOG_DIR="$ROOT/logs"
ASTROBOT_WORKDIR="$ROOT/astrobot"
ASTROBOT_BIN="$RUNTIME/astrbot/bin/astrbot"
ASTROBOT_PID_FILE="$RUN_DIR/astrbot.pid"
ASTROBOT_LOG="$LOG_DIR/astrbot.log"
SNOWLUMA_DIR="$ROOT/snowluma-live"
SNOWLUMA_COMPOSE="$SNOWLUMA_DIR/compose.yml"
SNOWLUMA_ENV="$SNOWLUMA_DIR/.env"
SNOWLUMA_CONTAINER="${SNOWLUMA_CONTAINER:-snowluma}"
ARCHIVE_ROOT="${XTWBOT_ARCHIVE_ROOT:-$(dirname "$ROOT")/xtw_bot_archive}"
MAINTENANCE_LOCK="$RUN_DIR/maintenance.lock"
START_TIMEOUT="${XTWBOT_START_TIMEOUT:-60}"
STOP_TIMEOUT="${XTWBOT_STOP_TIMEOUT:-30}"

umask 077

log() {
  printf '%s [%s] %s\n' "$(date '+%F %T')" "${SCRIPT_NAME:-xtwbot}" "$*"
}

warn() {
  printf 'warning: %s\n' "$*" >&2
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

is_positive_integer() {
  [[ "$1" =~ ^[1-9][0-9]*$ ]]
}

is_positive_integer "$START_TIMEOUT" || die "XTWBOT_START_TIMEOUT must be a positive integer"
is_positive_integer "$STOP_TIMEOUT" || die "XTWBOT_STOP_TIMEOUT must be a positive integer"

require_commands() {
  local command
  for command in "$@"; do
    command -v "$command" >/dev/null 2>&1 || die "required command not found: $command"
  done
}

ensure_runtime_dirs() {
  mkdir -p "$RUN_DIR" "$LOG_DIR"
}

ensure_astrbot_layout() {
  [[ -d "$ASTROBOT_WORKDIR" ]] || die "AstrBot workspace not found: $ASTROBOT_WORKDIR"
  [[ -x "$ASTROBOT_BIN" ]] || die "AstrBot executable not found: $ASTROBOT_BIN"
  [[ -f "$ASTROBOT_WORKDIR/data/cmd_config.json" ]] || die "AstrBot config not found"
  ensure_runtime_dirs
}

ensure_snowluma_layout() {
  [[ -f "$SNOWLUMA_COMPOSE" ]] || die "SnowLuma compose file not found: $SNOWLUMA_COMPOSE"
  [[ -f "$SNOWLUMA_ENV" ]] || die "SnowLuma environment file not found: $SNOWLUMA_ENV"
  [[ -d "$ROOT/snowluma/data" ]] || die "SnowLuma data directory not found"
  [[ -d "$ROOT/snowluma/qq-data" ]] || die "SnowLuma QQ data directory not found"
  [[ -d "$RUNTIME/home/.config/QQ" ]] || die "QQ login data directory not found"
  ensure_runtime_dirs
}

ensure_active_layout() {
  ensure_astrbot_layout
  ensure_snowluma_layout
}

clear_proxy_env() {
  local var
  for var in \
    http_proxy https_proxy ftp_proxy all_proxy socks_proxy \
    HTTP_PROXY HTTPS_PROXY FTP_PROXY ALL_PROXY SOCKS_PROXY \
    no_proxy NO_PROXY; do
    unset "$var" 2>/dev/null || true
  done
  export no_proxy="*"
  export NO_PROXY="*"
}

acquire_maintenance_lock() {
  require_commands flock
  mkdir -p "$RUN_DIR"
  exec {MAINTENANCE_LOCK_FD}>"$MAINTENANCE_LOCK"
  if ! flock -n "$MAINTENANCE_LOCK_FD"; then
    die "another xtw_bot maintenance command is running (lock: $MAINTENANCE_LOCK)"
  fi
}

pid_alive() {
  local pid=${1:-}
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] && kill -0 "$pid" 2>/dev/null
}

pid_cmdline() {
  local pid=$1
  tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true
}

astrbot_pid_matches() {
  local pid=$1
  pid_alive "$pid" || return 1
  [[ "$(pid_cmdline "$pid")" == *"$ASTROBOT_BIN run"* ]]
}

astrbot_pids() {
  local proc pid cmdline
  for proc in /proc/[0-9]*; do
    [[ -r "$proc/cmdline" ]] || continue
    pid=${proc##*/}
    cmdline=$(pid_cmdline "$pid")
    if [[ "$cmdline" == *"$ASTROBOT_BIN run"* ]]; then
      printf '%s\n' "$pid"
    fi
  done
}

astrbot_pid() {
  local pid
  if [[ -s "$ASTROBOT_PID_FILE" ]]; then
    pid=$(<"$ASTROBOT_PID_FILE")
    if astrbot_pid_matches "$pid"; then
      printf '%s\n' "$pid"
      return 0
    fi
  fi

  mapfile -t found < <(astrbot_pids)
  ((${#found[@]} > 0)) || return 1
  printf '%s\n' "${found[0]}"
}

astrbot_running() {
  astrbot_pid >/dev/null 2>&1
}

refresh_astrbot_pid_file() {
  mapfile -t found < <(astrbot_pids)
  case ${#found[@]} in
    0)
      rm -f "$ASTROBOT_PID_FILE"
      return 1
      ;;
    1)
      printf '%s\n' "${found[0]}" >"$ASTROBOT_PID_FILE"
      printf '%s\n' "${found[0]}"
      ;;
    *)
      warn "multiple AstrBot processes found: ${found[*]}"
      return 2
      ;;
  esac
}

tcp_open() {
  local host=$1 port=$2
  (echo >/dev/tcp/"$host"/"$port") 2>/dev/null
}

wait_for_tcp() {
  local host=$1 port=$2 timeout_seconds=$3
  local deadline=$((SECONDS + timeout_seconds))
  while ((SECONDS < deadline)); do
    if tcp_open "$host" "$port"; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

http_reachable() {
  local url=$1
  curl --silent --show-error --output /dev/null --max-time 2 "$url" 2>/dev/null
}

wait_for_http() {
  local url=$1 timeout_seconds=$2
  local deadline=$((SECONDS + timeout_seconds))
  while ((SECONDS < deadline)); do
    if http_reachable "$url"; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

terminate_astrbot_pid() {
  local pid=$1
  local pgid=""
  astrbot_pid_matches "$pid" || return 0
  pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ' || true)

  kill -TERM "$pid" 2>/dev/null || true
  local deadline=$((SECONDS + STOP_TIMEOUT))
  while astrbot_pid_matches "$pid" && ((SECONDS < deadline)); do
    sleep 0.25
  done

  if astrbot_pid_matches "$pid" && [[ "$pgid" == "$pid" ]]; then
    kill -TERM -- "-$pgid" 2>/dev/null || true
    deadline=$((SECONDS + 5))
    while astrbot_pid_matches "$pid" && ((SECONDS < deadline)); do
      sleep 0.25
    done
  fi

  if astrbot_pid_matches "$pid"; then
    warn "AstrBot PID $pid did not stop gracefully; sending SIGKILL"
    if [[ "$pgid" == "$pid" ]]; then
      kill -KILL -- "-$pgid" 2>/dev/null || true
    else
      kill -KILL "$pid" 2>/dev/null || true
    fi
  fi
}

start_astrbot() {
  ensure_astrbot_layout
  require_commands setsid curl

  mapfile -t found < <(astrbot_pids)
  if ((${#found[@]} == 1)); then
    printf '%s\n' "${found[0]}" >"$ASTROBOT_PID_FILE"
    log "AstrBot already running (PID ${found[0]})"
    return 0
  fi
  if ((${#found[@]} > 1)); then
    die "multiple AstrBot processes found: ${found[*]}"
  fi

  rm -f "$ASTROBOT_PID_FILE"
  local maintenance_lock_fd="${MAINTENANCE_LOCK_FD:-}"
  nohup bash -c '
    lock_fd=$1
    command_path=$2
    if [[ "$lock_fd" =~ ^[0-9]+$ ]]; then
      eval "exec ${lock_fd}>&-"
    fi
    exec setsid "$command_path"
  ' _ "$maintenance_lock_fd" "$ROOT/bin/run-astrbot" >>"$ASTROBOT_LOG" 2>&1 </dev/null &
  local pid=$!
  printf '%s\n' "$pid" >"$ASTROBOT_PID_FILE"
  log "starting AstrBot (PID $pid)"

  if ! wait_for_tcp 127.0.0.1 8001 "$START_TIMEOUT" || \
     ! wait_for_tcp 127.0.0.1 6200 "$START_TIMEOUT"; then
    warn "AstrBot did not become ready within ${START_TIMEOUT}s"
    tail -n 30 "$ASTROBOT_LOG" >&2 2>/dev/null || true
    terminate_astrbot_pid "$pid"
    rm -f "$ASTROBOT_PID_FILE"
    return 1
  fi

  refresh_astrbot_pid_file >/dev/null
  log "AstrBot ready (PID $(<"$ASTROBOT_PID_FILE"), ports 6200/8001)"
}

stop_astrbot() {
  mapfile -t found < <(astrbot_pids)
  if ((${#found[@]} == 0)); then
    rm -f "$ASTROBOT_PID_FILE"
    log "AstrBot is not running"
    return 0
  fi

  local pid
  for pid in "${found[@]}"; do
    log "stopping AstrBot (PID $pid)"
    terminate_astrbot_pid "$pid"
  done
  rm -f "$ASTROBOT_PID_FILE"
  log "AstrBot stopped"
}

docker_ready() {
  docker info >/dev/null 2>&1
}

snowluma_compose() {
  docker compose \
    --project-directory "$SNOWLUMA_DIR" \
    --env-file "$SNOWLUMA_ENV" \
    -f "$SNOWLUMA_COMPOSE" "$@"
}

snowluma_exists() {
  docker inspect "$SNOWLUMA_CONTAINER" >/dev/null 2>&1
}

snowluma_running() {
  [[ "$(docker inspect -f '{{.State.Running}}' "$SNOWLUMA_CONTAINER" 2>/dev/null || true)" == "true" ]]
}

start_snowluma() {
  ensure_snowluma_layout
  require_commands docker curl
  docker_ready || die "Docker daemon is unavailable"
  snowluma_compose config --quiet

  if snowluma_running && http_reachable http://127.0.0.1:5099/; then
    log "SnowLuma already running and ready"
    return 0
  fi

  log "starting SnowLuma"
  snowluma_compose up -d --remove-orphans
  local deadline=$((SECONDS + START_TIMEOUT))
  while ((SECONDS < deadline)); do
    if snowluma_running && http_reachable http://127.0.0.1:5099/; then
      log "SnowLuma ready (WebUI 127.0.0.1:5099)"
      return 0
    fi
    sleep 0.5
  done

  warn "SnowLuma did not become ready within ${START_TIMEOUT}s"
  docker logs --tail 40 "$SNOWLUMA_CONTAINER" >&2 2>/dev/null || true
  return 1
}

stop_snowluma() {
  ensure_runtime_dirs
  require_commands docker
  docker_ready || die "Docker daemon is unavailable"

  if ! snowluma_running; then
    log "SnowLuma is not running"
    return 0
  fi

  log "stopping SnowLuma"
  if [[ -f "$SNOWLUMA_COMPOSE" && -f "$SNOWLUMA_ENV" ]]; then
    snowluma_compose stop --timeout "$STOP_TIMEOUT"
  else
    warn "SnowLuma Compose files are unavailable; stopping the container directly"
    docker stop --time "$STOP_TIMEOUT" "$SNOWLUMA_CONTAINER" >/dev/null
  fi
  if snowluma_running; then
    warn "SnowLuma container is still running after stop"
    return 1
  fi
  log "SnowLuma stopped"
}

validate_service_name() {
  case "$1" in
    all|astrbot|snowluma) ;;
    *) die "unknown service '$1' (expected: all, astrbot, snowluma)" ;;
  esac
}
