"""Regression tests for the loopback-only AstrBot observer."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.ui.server.astrbot_connector import AstrBotConnectorError, LocalAstrBotConnector


class _DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = b"local astrbot test dashboard"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


def _start_dashboard() -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DashboardHandler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    return server, worker, f"http://127.0.0.1:{server.server_port}"


def test_connector_reports_connected_dashboard_and_redacted_log_tail(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "astrbot.log").write_text(
        "[INFO] AstrBot started\n[WARN] Authorization: local-test-secret-value\n",
        encoding="utf-8",
    )
    server, worker, endpoint = _start_dashboard()
    try:
        connector = LocalAstrBotConnector(tmp_path, endpoint=endpoint, data_dir=data_dir)
        snapshot = connector.snapshot()
    finally:
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()

    assert snapshot["status"] == "CONNECTED"
    assert snapshot["capture_mode"] == "PARTIAL"
    assert snapshot["hooks"] == "NOT_CONNECTED"
    assert snapshot["dispatch"] == "DISABLED"
    rendered = "\n".join(item["message"] for item in snapshot["logs"])
    assert "[REDACTED_SECRET]" in rendered
    assert "local-test-secret-value" not in rendered
    assert all(item["request_id"] is None for item in snapshot["logs"])


def test_connector_rejects_non_loopback_or_credentialed_endpoint(tmp_path: Path) -> None:
    with pytest.raises(AstrBotConnectorError, match="loopback"):
        LocalAstrBotConnector(tmp_path, endpoint="http://example.invalid:6185")
    with pytest.raises(AstrBotConnectorError, match="credentials"):
        LocalAstrBotConnector(tmp_path, endpoint="http://user:password@127.0.0.1:6185")
