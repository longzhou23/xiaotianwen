#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/common-deploy.sh"

require_linux
require_layout
need git
need python3
need awk

INSTALL_SYSTEM_DEPS=${INSTALL_SYSTEM_DEPS:-0}
if [[ "$INSTALL_SYSTEM_DEPS" == 1 ]]; then
  need sudo
  sudo apt-get update
  compose_package=''
  for candidate in docker-compose-v2 docker-compose-plugin docker-compose; do
    if apt-cache show "$candidate" >/dev/null 2>&1; then
      compose_package=$candidate
      break
    fi
  done
  [[ -n "$compose_package" ]] || die 'no Docker Compose package is available from the configured apt repositories'
  sudo apt-get install -y ca-certificates curl git rsync python3 python3-venv python3-pip openssl docker.io "$compose_package"
fi

need rsync

# Writing plugin code or rendering configuration underneath a running process
# can leave a half-updated runtime. The update entrypoint stops both services
# before invoking this installer.
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  if container_running astrbot || container_running snowluma; then
    die 'install.sh requires stopped services; use up-latest.sh for an online instance update'
  fi
fi

# AstrBot runs as root and can leave cmd_config.json or plugin config files
# mode 600 after shutdown. A non-root deploy user cannot read those bind-mounted
# files directly, but Docker can copy them out of the stopped container. Import
# only the configuration files that the renderer must inspect; databases and
# user data remain untouched.
import_stopped_runtime_configs() {
  command -v docker >/dev/null 2>&1 || return 0
  docker info >/dev/null 2>&1 || return 0
  docker inspect astrbot >/dev/null 2>&1 || return 0
  [[ "$(docker inspect -f '{{.State.Running}}' astrbot 2>/dev/null || true)" == false ]] || return 0

  local target relative temp_dir basename
  local targets=("$RUNTIME_DIR/astrobot/data/cmd_config.json")
  while IFS= read -r -d '' target; do
    targets+=("$target")
  done < <(find "$RUNTIME_DIR/astrobot/data/config" -maxdepth 1 -type f -name '*.json' ! -readable -print0 2>/dev/null)

  for target in "${targets[@]}"; do
    [[ -f "$target" && ! -r "$target" ]] || continue
    relative=${target#"$RUNTIME_DIR/astrobot/data"}
    basename=$(basename -- "$target")
    temp_dir=$(mktemp -d)
    if ! docker cp "astrbot:/AstrBot/data$relative" "$temp_dir/" >/dev/null; then
      rm -rf "$temp_dir"
      die "cannot read deployment-managed config from stopped astrbot container: $relative"
    fi
    chmod 600 "$temp_dir/$basename"
    mv -f "$temp_dir/$basename" "$target"
    rmdir "$temp_dir" 2>/dev/null || true
    log "imported root-owned config through stopped astrbot container: $relative"
  done
}

import_stopped_runtime_configs

mkdir -p \
  "$RUNTIME_DIR" \
  "$DEPLOY_STATE_DIR" \
  "$RUNTIME_DIR/astrobot/data" \
  "$RUNTIME_DIR/snowluma/data" \
  "$RUNTIME_DIR/snowluma/qq-config" \
  "$RUNTIME_DIR/snowluma/qq-data"

RESTORE_INSTANCE=${RESTORE_INSTANCE:-auto}
case "$RESTORE_INSTANCE" in
  auto)
    if [[ -f "$INSTANCE_MARKER" ]]; then
      RESTORE_INSTANCE=0
    elif [[ -f "$RUNTIME_DIR/astrobot/data/cmd_config.json" && -f "$RUNTIME_DIR/astrobot/data/data_v4.db" ]]; then
      # Adopt a runtime created by the earlier deployment flow. Treat it as
      # authoritative instead of silently restoring an older private snapshot.
      {
        printf 'restored_at=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
        printf 'private_commit=legacy-runtime-adopted\n'
      } >"$INSTANCE_MARKER"
      chmod 600 "$INSTANCE_MARKER"
      RESTORE_INSTANCE=0
      log 'existing live runtime adopted; private snapshot restore will not run automatically'
    else
      RESTORE_INSTANCE=1
    fi
    ;;
  0|1) ;;
  *) die "RESTORE_INSTANCE must be auto, 0, or 1" ;;
esac

restore_instance_data() {
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    if container_running astrbot || container_running snowluma; then
      die 'instance restore requires stopped astrbot and snowluma containers'
    fi
  fi

  # A restore is additive and intentionally avoids --delete. It is performed
  # only on first deployment or when RESTORE_INSTANCE=1 is explicitly set.
  # Normal image/plugin updates must never roll live databases back to a Git
  # snapshot.
  if [[ -d "$PRIVATE_DIR/instance/astrobot-data" ]]; then
    rsync -a "$PRIVATE_DIR/instance/astrobot-data"/ "$RUNTIME_DIR/astrobot/data"/
  fi
  if [[ -d "$PRIVATE_DIR/instance/snowluma-data/data" ]]; then
    rsync -a "$PRIVATE_DIR/instance/snowluma-data/data"/ "$RUNTIME_DIR/snowluma/data"/
  fi
  if [[ -d "$PRIVATE_DIR/instance/qq-config" ]]; then
    rsync -a "$PRIVATE_DIR/instance/qq-config"/ "$RUNTIME_DIR/snowluma/qq-config"/
  fi
  if [[ -d "$PRIVATE_DIR/instance/qq-data" ]]; then
    rsync -a "$PRIVATE_DIR/instance/qq-data"/ "$RUNTIME_DIR/snowluma/qq-data"/
  fi

  {
    printf 'restored_at=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    printf 'private_commit=%s\n' "$(git -C "$PRIVATE_DIR" rev-parse HEAD 2>/dev/null || printf unknown)"
  } >"$INSTANCE_MARKER"
  chmod 600 "$INSTANCE_MARKER"
  log 'private instance snapshot restored into runtime'
}

if [[ "$RESTORE_INSTANCE" == 1 ]]; then
  restore_instance_data
else
  log "live runtime retained; private instance snapshot restore skipped ($INSTANCE_MARKER exists)"
fi

# Resolve credential placeholders in runtime JSON files. Values are read as a
# strict dotenv file and are never evaluated as shell code or printed.
apply_runtime_secrets() {
  require_secret_file

  python3 - "$SECRET_FILE" "$RUNTIME_DIR" "$DEPLOY_STATE_DIR/unresolved-secrets.txt" <<'PY'
import json
import re
import sys
from pathlib import Path

secret_path = Path(sys.argv[1])
runtime = Path(sys.argv[2])
report_path = Path(sys.argv[3])
name_pattern = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
placeholder_pattern = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")

values = {}
for number, raw_line in enumerate(secret_path.read_text(encoding="utf-8-sig").splitlines(), 1):
    line = raw_line.strip()
    if not line or line.startswith("#"):
        continue
    if "=" not in line:
        raise SystemExit(f"invalid secret line {number}: expected NAME=value")
    name, value = line.split("=", 1)
    name = name.strip()
    value = value.strip()
    if not name_pattern.fullmatch(name):
        raise SystemExit(f"invalid secret variable name on line {number}")
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    values[name] = value

astrbot_token = values.get("SECRET_WS_REVERSE_TOKEN", "")
snowluma_token = values.get("SECRET_ACCESSTOKEN", "")
if not astrbot_token or not snowluma_token:
    raise SystemExit("OneBot secret pair is missing from host secret file")
if astrbot_token != snowluma_token:
    raise SystemExit("OneBot secret pair does not match")

paths = []
for candidate in [
    runtime / "astrobot/data/cmd_config.json",
    runtime / "astrobot/data/config",
    runtime / "snowluma/data/config",
]:
    if candidate.is_file():
        paths.append(candidate)
    elif candidate.is_dir():
        paths.extend(sorted(candidate.rglob("*.json")))

for path in paths:
    text = path.read_text(encoding="utf-8-sig")

    def replace(match):
        name = match.group(1)
        if name in values and values[name] != "":
            # Placeholders in the instance templates live inside JSON strings;
            # escape the replacement as JSON string content.
            return json.dumps(values[name], ensure_ascii=False)[1:-1]
        return match.group(0)

    updated = placeholder_pattern.sub(replace, text)
    document = json.loads(updated)
    changed_structurally = False

    if path.name == "cmd_config.json" and isinstance(document, dict):
        source_secret_names = {
            "deepseek-responses": "SECRET_DEEPSEEK_API_KEY",
            "siliconflow": "SECRET_SILICONFLOW_API_KEY",
            "openai-responses": "SECRET_OPENAI_API_KEY",
        }
        for source in document.get("provider_sources", []):
            secret_name = source_secret_names.get(source.get("id"))
            if secret_name and values.get(secret_name):
                # AstrBot 4.27.x 的 OpenAI 适配器按多 Key 列表处理
                # provider source；单个字符串会在请求时触发
                # ``'str' object has no attribute 'copy'``，导致 VLM、
                # 情绪分析和普通 SiliconFlow 请求全部失败。
                source["key"] = [values[secret_name]]
                changed_structurally = True
        for provider in document.get("provider", []):
            provider_id = provider.get("id")
            if provider_id == "ollama-bge-m3" and values.get("SECRET_SILICONFLOW_API_KEY"):
                provider["embedding_api_key"] = values["SECRET_SILICONFLOW_API_KEY"]
                changed_structurally = True
            if provider_id == "BAAI/bge-reranker-v2-m3" and values.get("SECRET_SILICONFLOW_API_KEY"):
                provider["rerank_api_key"] = values["SECRET_SILICONFLOW_API_KEY"]
                changed_structurally = True
        tavily = values.get("SECRET_TAVILY_API_KEY")
        if tavily:
            document.setdefault("provider_settings", {})["websearch_tavily_key"] = [tavily]
            changed_structurally = True
        for platform in document.get("platform", []):
            if platform.get("type") == "aiocqhttp":
                platform["ws_reverse_host"] = "0.0.0.0"
                platform["ws_reverse_port"] = 8001
                platform["ws_reverse_token"] = astrbot_token
                changed_structurally = True

    if path.name.startswith("onebot") and isinstance(document, dict):
        clients = document.get("networks", {}).get("wsClients", [])
        for client in clients:
            # Per-account SnowLuma snapshots may omit the display name.  A
            # single client is still the AstrBot connection for this instance.
            if client.get("name") == "astrbot" or len(clients) == 1:
                client["url"] = "ws://astrbot:8001/ws"
                client["accessToken"] = snowluma_token
                changed_structurally = True

    if path.name == "astrbot_plugin_astrmetry_config.json" and values.get("SECRET_ASTROMETRY_API_KEY"):
        document["APIkey"] = values["SECRET_ASTROMETRY_API_KEY"]
        changed_structurally = True
    if path.name == "antipromptinjector_config.json" and values.get("ANTIPROMPTINJECTOR_WEBUI_TOKEN"):
        document["webui_token"] = values["ANTIPROMPTINJECTOR_WEBUI_TOKEN"]
        changed_structurally = True

    if changed_structurally:
        updated = json.dumps(document, ensure_ascii=False, indent=2) + "\n"

    if updated != text:
        temporary = path.with_name(path.name + ".rendering")
        temporary.write_text(updated, encoding="utf-8", newline="\n")
        temporary.replace(path)

unresolved = set()
for path in paths:
    unresolved.update(placeholder_pattern.findall(path.read_text(encoding="utf-8-sig")))

report_path.parent.mkdir(parents=True, exist_ok=True)
report_path.write_text("\n".join(sorted(unresolved)) + ("\n" if unresolved else ""), encoding="utf-8")
PY

  chmod 600 "$DEPLOY_STATE_DIR/unresolved-secrets.txt"
  if [[ -s "$DEPLOY_STATE_DIR/unresolved-secrets.txt" ]]; then
    log "runtime secrets rendered; optional unresolved variable names recorded in $DEPLOY_STATE_DIR/unresolved-secrets.txt"
  else
    log 'all runtime credential placeholders resolved from host secret file'
  fi
}

apply_runtime_secrets

mkdir -p "$RUNTIME_DIR/astrobot/data/plugins"
LOCK_FILE="$PRIVATE_DIR/plugins.lock.yaml"
[[ -f "$LOCK_FILE" ]] || die "plugin lock not found: $LOCK_FILE"

# 读取本项目生成的受限 YAML 形状：每项包含 source 和 enabled。
# 不接受未知根路径，避免锁文件把任意宿主机目录复制进运行时。
mapfile -t enabled_sources < <(awk '
  /^  - name:/ { if (source != "" && enabled == "true") print source; source=""; enabled=""; next }
  /^    source:/ { source=$2; next }
  /^    enabled:/ { enabled=$2; next }
  END { if (source != "" && enabled == "true") print source }
' "$LOCK_FILE")
((${#enabled_sources[@]} > 0)) || die "plugin lock contains no enabled plugins: $LOCK_FILE"

for source_rel in "${enabled_sources[@]}"; do
  case "$source_rel" in
    public/*)
      source_dir="$PUBLIC_DIR/${source_rel#public/}"
      ;;
    private/*)
      source_dir="$PRIVATE_DIR/${source_rel#private/}"
      ;;
    *)
      die "unsupported plugin source in lock: $source_rel"
      ;;
  esac
  [[ -d "$source_dir" ]] || die "locked plugin source not found: $source_dir"
  target_dir="$RUNTIME_DIR/astrobot/data/plugins/${source_dir##*/}"
  mkdir -p "$target_dir"
  # The lock owns plugin source code, so stale files inside this exact plugin
  # directory are removed. Unknown/manual plugin directories are preserved.
  # Python bytecode caches are generated by the root-running AstrBot
  # container. They are runtime artifacts, not lock-owned source files, and
  # must not make an otherwise safe plugin sync fail for a non-root deployer.
  rsync -a --delete --exclude='__pycache__/' --exclude='*.pyc' "$source_dir"/ "$target_dir"/
  log "plugin restored: ${source_dir##*/}"
done

if [[ -f "$SECRET_FILE" ]]; then
  chmod 600 "$SECRET_FILE"
  log "secret file present: $SECRET_FILE"
else
  log "secret file not present; code/data layout installed, service start is deferred"
fi

log "installation layout prepared under $PROJECT_ROOT"
log "runtime will use the official latest AstrBot and SnowLuma container images"
