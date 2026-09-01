"""Read-only observation of a local AstrBot test instance.

This connector deliberately stops at a loopback Dashboard health check and a
bounded, redacted log tail. It does not log in, send OneBot events, invoke a
Provider, read databases, or register AstrBot hooks. That keeps connecting the
console separate from changing the instance under test.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import ProxyHandler, Request, build_opener

from tests.harness.redact import redact_text


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_DEFAULT_DASHBOARD_PORT = 6185
_MAX_LOG_BYTES = 128 * 1024
_MAX_LOG_LINES = 160
_MAX_LOG_LINE_CHARS = 2_000
_LOG_NAMES = frozenset({"astrbot.log", "astrbot_test_stdout-current.log"})
_CONNECT_LOG_PATTERN = re.compile(r"^astrbot_test_(?:stdout|stderr)-connect-[0-9]{8}-[0-9]{6}\.log$")
_LEVEL_PATTERN = re.compile(r"\[(DEBUG|DBUG|INFO|WARN|WARNING|ERROR|CRITICAL|TRACE)\]", re.IGNORECASE)
_TIMESTAMP_PATTERN = re.compile(r"^\[?(?P<value>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\]?")
_SHORT_TIMESTAMP_PATTERN = re.compile(r"^\[?(?P<value>\d{2}:\d{2}:\d{2}(?:[.,]\d+)?)\]?")
_ANSI_PATTERN = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_ABSOLUTE_PATH_PATTERN = re.compile(r"(?i)(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/])[^\r\n'\"<>]+")


class AstrBotConnectorError(ValueError):
    """The local connector configuration is invalid."""


@dataclass(frozen=True, slots=True)
class AstrBotTarget:
    """A local test target with a user-facing label instead of a filesystem path."""

    label: str
    data_dir: Path | None
    endpoint: str


def _loopback_endpoint(value: str) -> str:
    """Validate and normalize an HTTP endpoint to a loopback-only URL."""

    raw = value.strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme != "http" or parsed.hostname not in _LOOPBACK_HOSTS:
        raise AstrBotConnectorError("AstrBot endpoint must be an http loopback URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AstrBotConnectorError("AstrBot endpoint must not contain credentials or query data")
    if parsed.path not in {"", "/"}:
        raise AstrBotConnectorError("AstrBot endpoint must point to the Dashboard root")
    port = parsed.port or _DEFAULT_DASHBOARD_PORT
    if not 1 <= port <= 65_535:
        raise AstrBotConnectorError("AstrBot endpoint port is invalid")
    host = parsed.hostname
    display_host = "[::1]" if host == "::1" else "127.0.0.1"
    return f"http://{display_host}:{port}"


def _dashboard_port(data_dir: Path | None) -> int:
    if data_dir is None:
        return _DEFAULT_DASHBOARD_PORT
    config_path = data_dir / "cmd_config.json"
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        dashboard = payload.get("dashboard", {})
        port = dashboard.get("port", _DEFAULT_DASHBOARD_PORT) if isinstance(dashboard, dict) else _DEFAULT_DASHBOARD_PORT
        if isinstance(port, int) and 1 <= port <= 65_535:
            return port
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
        pass
    return _DEFAULT_DASHBOARD_PORT


def _resolve_data_dir(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    path = Path(value).expanduser()
    try:
        return path.resolve()
    except OSError as exc:
        raise AstrBotConnectorError("AstrBot data directory cannot be resolved") from exc


def discover_target(repository_root: Path, *, endpoint: str | None = None, data_dir: str | Path | None = None) -> AstrBotTarget:
    """Find the disposable local xtw test instance without reading private state."""

    configured_data_dir = _resolve_data_dir(data_dir or os.environ.get("XTW_ASTRBOT_DATA_DIR"))
    configured_endpoint = endpoint or os.environ.get("XTW_ASTRBOT_URL")
    if configured_data_dir is not None:
        return AstrBotTarget(
            label="configured local AstrBot",
            data_dir=configured_data_dir,
            endpoint=_loopback_endpoint(configured_endpoint or f"http://127.0.0.1:{_dashboard_port(configured_data_dir)}"),
        )
    if configured_endpoint:
        return AstrBotTarget(label="configured local AstrBot", data_dir=None, endpoint=_loopback_endpoint(configured_endpoint))

    bot_root = repository_root.resolve().parents[1]
    candidates = (
        ("xtw local test instance", bot_root / "projects" / "astrbot_test_server" / "data"),
        ("xtw recovered instance (read-only target)", bot_root / "recovery" / "xtw_bot" / "astrobot" / "data"),
    )
    for label, candidate in candidates:
        if candidate.is_dir():
            return AstrBotTarget(label=label, data_dir=candidate, endpoint=f"http://127.0.0.1:{_dashboard_port(candidate)}")
    return AstrBotTarget(label="local AstrBot not discovered", data_dir=None, endpoint=f"http://127.0.0.1:{_DEFAULT_DASHBOARD_PORT}")


def _log_candidates(target: AstrBotTarget) -> tuple[Path, ...]:
    if target.data_dir is None:
        return ()
    data_dir = target.data_dir
    log_dir = data_dir / "logs"
    candidates = [log_dir / "astrbot.log", log_dir / "astrbot_test_stdout-current.log"]
    try:
        candidates.extend(path for path in log_dir.glob("astrbot_test_*connect-*.log") if _CONNECT_LOG_PATTERN.fullmatch(path.name))
    except OSError:
        pass
    if data_dir.name == "data":
        candidates.extend(
            (
                data_dir.parent / "astrbot_test_stdout-current.log",
                data_dir.parent.parent / "logs" / "astrbot.log",
            )
        )
    return tuple(path for path in candidates if path.name in _LOG_NAMES or _CONNECT_LOG_PATTERN.fullmatch(path.name))


def _select_log_path(target: AstrBotTarget) -> Path | None:
    existing = [path for path in _log_candidates(target) if path.is_file()]
    if not existing:
        return None
    return max(existing, key=lambda path: path.stat().st_mtime)


def _log_level(line: str) -> str:
    match = _LEVEL_PATTERN.search(line)
    if not match:
        return "INFO"
    level = match.group(1).upper()
    return "DEBUG" if level == "DBUG" else level


def _log_timestamp(line: str) -> str | None:
    stripped = line.strip()
    match = _TIMESTAMP_PATTERN.match(stripped) or _SHORT_TIMESTAMP_PATTERN.match(stripped)
    return match.group("value") if match else None


def _sanitize_log_line(line: str) -> str:
    """Keep useful log wording while removing terminal markup and machine paths."""

    cleaned = _ANSI_PATTERN.sub("", line).strip()
    cleaned = _ABSOLUTE_PATH_PATTERN.sub("[LOCAL_PATH]", cleaned)
    return cleaned


def _read_log_tail(path: Path | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if path is None:
        return [], {"status": "NOT_FOUND", "line_count": 0}
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - _MAX_LOG_BYTES))
            raw = handle.read(_MAX_LOG_BYTES)
        text = raw.decode("utf-8", errors="replace")
    except (OSError, UnicodeError) as exc:
        return [], {"status": "READ_ERROR", "error_class": type(exc).__name__, "line_count": 0}

    lines = text.splitlines()[-_MAX_LOG_LINES:]
    records: list[dict[str, Any]] = []
    for line in lines:
        safe_line = redact_text(_sanitize_log_line(line))
        if len(safe_line) > _MAX_LOG_LINE_CHARS:
            safe_line = safe_line[:_MAX_LOG_LINE_CHARS] + "…"
        records.append(
            {
                "source": "ASTRBOT",
                "level": _log_level(safe_line),
                "timestamp": _log_timestamp(safe_line),
                "request_id": None,
                "capture_mode": "PARTIAL",
                "message": safe_line,
            }
        )
    return records, {"status": "AVAILABLE", "line_count": len(records), "truncated": size > _MAX_LOG_BYTES}


class LocalAstrBotConnector:
    """A bounded, read-only connector for local Dashboard and log health."""

    def __init__(self, repository_root: Path, *, endpoint: str | None = None, data_dir: str | Path | None = None) -> None:
        self.target = discover_target(repository_root, endpoint=endpoint, data_dir=data_dir)
        self._opener = build_opener(ProxyHandler({}))

    def _probe_dashboard(self) -> dict[str, Any]:
        request = Request(self.target.endpoint + "/", headers={"Accept": "text/html", "Cache-Control": "no-cache"})
        try:
            with self._opener.open(request, timeout=1.5) as response:
                response.read(256)
                return {"status": "CONNECTED", "http_status": int(response.status)}
        except HTTPError as exc:
            # A 401/403 still proves that the local Dashboard is reachable.
            return {"status": "CONNECTED", "http_status": int(exc.code)}
        except (URLError, TimeoutError, OSError) as exc:
            return {"status": "NOT_CONNECTED", "error_class": type(exc).__name__}

    def snapshot(self) -> dict[str, Any]:
        dashboard = self._probe_dashboard()
        log_path = _select_log_path(self.target)
        logs, log_status = _read_log_tail(log_path)
        has_log = log_status["status"] == "AVAILABLE"
        status = "CONNECTED" if dashboard["status"] == "CONNECTED" else ("PARTIAL" if has_log else "NOT_CONNECTED")
        capture_mode = "PARTIAL" if dashboard["status"] == "CONNECTED" or has_log else "NOT_CONNECTED"
        if dashboard["status"] == "CONNECTED":
            note = "只读连接已建立：Dashboard 可达；当前未注册 AstrBot Hook，也不会派发测试输入。"
        elif has_log:
            note = "Dashboard 当前不可达，但发现本地脱敏日志尾部；实例运行状态仍未确认。"
        else:
            note = "未发现可达的本地 AstrBot Dashboard 或可读日志。"
        return {
            "status": status,
            "capture_mode": capture_mode,
            "instance": self.target.label,
            "endpoint": self.target.endpoint,
            "scope": "read_only_dashboard_and_redacted_log_tail",
            "dashboard": dashboard,
            "log": log_status,
            "logs": logs,
            "hooks": "NOT_CONNECTED",
            "dispatch": "DISABLED",
            "checked_at": time.time(),
            "note": note,
        }

    def descriptor(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        return {key: snapshot[key] for key in ("status", "capture_mode", "instance", "endpoint", "scope", "hooks", "dispatch", "note")}
