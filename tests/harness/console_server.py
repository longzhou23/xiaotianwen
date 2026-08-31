"""Loopback-only Local Test Console for P0 synthetic replay inspection."""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .replay import ReplayEngine, ReplayResult, build_interactive_case


ALLOWED_ROUTES = {"chat", "agent", "decision", "proactive", "vision", "background"}
MAX_INPUT_CHARS = 20_000
MAX_RUNS = 30


@dataclass(slots=True)
class ConsoleState:
    """Bounded in-memory state; no browser input is persisted to repository files."""

    engine: ReplayEngine = field(default_factory=ReplayEngine)
    max_runs: int = MAX_RUNS
    session_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    csrf_token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    _runs: dict[str, ReplayResult] = field(default_factory=dict, init=False, repr=False)
    _counter: int = field(default=0, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def create_run(self, payload: dict[str, Any]) -> ReplayResult:
        text = payload.get("text")
        route = payload.get("route", "chat")
        stream = payload.get("stream", False)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("text is required")
        if len(text) > MAX_INPUT_CHARS:
            raise ValueError(f"text exceeds {MAX_INPUT_CHARS} characters")
        if not isinstance(route, str) or route not in ALLOWED_ROUTES:
            raise ValueError("route is not allowed")
        if type(stream) is not bool:
            raise ValueError("stream must be a boolean")
        with self._lock:
            self._counter += 1
            case_id = f"p0-ui-{self._counter:03d}"
            result = self.engine.run_case(
                build_interactive_case(case_id=case_id, text=text, route=route, stream=stream),
                run_id=f"local-ui-run-{self._counter:03d}",
            )
            self._runs[result.run_id] = result
            while len(self._runs) > self.max_runs:
                self._runs.pop(next(iter(self._runs)))
            return result

    def list_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "run_id": result.run_id,
                    "case_id": result.case_id,
                    "title": result.title,
                    "passed": result.passed,
                    "summary": result.summary,
                    "capture_mode": "COMPLETE",
                }
                for result in reversed(tuple(self._runs.values()))
            ]

    def get_run(self, run_id: str) -> ReplayResult | None:
        with self._lock:
            return self._runs.get(run_id)


class _ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], state: ConsoleState) -> None:
        self.state = state
        super().__init__(address, _ConsoleHandler)


class _ConsoleHandler(BaseHTTPRequestHandler):
    server: _ConsoleServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        # The standard handler log could contain a browser-provided path/query.
        # P0 intentionally keeps console access logs out of stdout artifacts.
        del format, args

    def _send_json(self, status: HTTPStatus, payload: Any) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _send_text(self, status: HTTPStatus, text: str, content_type: str = "text/plain; charset=utf-8") -> None:
        encoded = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _cookie_token(self) -> str | None:
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
        except (KeyError, ValueError):
            return None
        morsel = cookies.get("xtw_local_console")
        return morsel.value if morsel is not None else None

    def _is_authenticated(self) -> bool:
        return secrets.compare_digest(self._cookie_token() or "", self.server.state.session_token)

    def _origin_is_local(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        parsed = urlparse(origin)
        expected = {"127.0.0.1", "localhost"}
        return parsed.scheme == "http" and parsed.hostname in expected and parsed.port == self.server.server_port

    def _post_is_authorized(self) -> bool:
        csrf = self.headers.get("X-Test-Console-CSRF", "")
        return self._is_authenticated() and self._origin_is_local() and secrets.compare_digest(
            csrf, self.server.state.csrf_token
        )

    def _read_json_body(self) -> dict[str, Any]:
        content_length = self.headers.get("Content-Length")
        try:
            size = int(content_length or "0")
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if size <= 0 or size > 64 * 1024:
            raise ValueError("request body size is not allowed")
        body = self.rfile.read(size)
        value = json.loads(body.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def _render_index(self) -> None:
        template_path = Path(__file__).with_name("console.html")
        template = template_path.read_text(encoding="utf-8")
        page = template.replace("{{CSRF_TOKEN}}", self.server.state.csrf_token)
        encoded = page.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Set-Cookie", f"xtw_local_console={self.server.state.session_token}; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._render_index()
            return
        if not parsed.path.startswith("/api/"):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not self._is_authenticated():
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "local session required"})
            return
        if parsed.path == "/api/runs":
            self._send_json(HTTPStatus.OK, {"runs": self.server.state.list_runs(), "capture_mode": "COMPLETE"})
            return
        parts = parsed.path.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "runs":
            result = self.server.state.get_run(parts[2])
            if result is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown run"})
                return
            if len(parts) == 3:
                self._send_json(HTTPStatus.OK, result.to_dict())
                return
            if len(parts) == 4 and parts[3] == "stream":
                self._serve_event_stream(result)
                return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def _serve_event_stream(self, result: ReplayResult) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            for event in result.trace:
                payload = json.dumps(event, ensure_ascii=False)
                self.wfile.write(f"event: trace\ndata: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
            self.wfile.write(b"event: complete\ndata: {}\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/runs":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if not self._post_is_authorized():
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "local origin, session and CSRF token required"})
            return
        try:
            result = self.server.state.create_run(self._read_json_body())
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.CREATED, result.to_dict())


class LocalConsole:
    """Lifecycle wrapper for tests and the CLI. It binds loopback only."""

    def __init__(self, *, host: str = "127.0.0.1", port: int = 0, state: ConsoleState | None = None) -> None:
        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("Local Test Console may only bind to loopback")
        self._server = _ConsoleServer((host, port), state or ConsoleState())
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/"

    @property
    def state(self) -> ConsoleState:
        return self._server.state

    def start(self) -> "LocalConsole":
        if self._thread is not None:
            return self
        self._thread = threading.Thread(target=self._server.serve_forever, name="xtw-local-test-console", daemon=True)
        self._thread.start()
        return self

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def __enter__(self) -> "LocalConsole":
        return self.start()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.close()
