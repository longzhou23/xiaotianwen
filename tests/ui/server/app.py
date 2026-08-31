"""A small, loopback-only Local Test Console built with Python's stdlib.

It is intentionally isolated from AstrBot, SnowLuma, Docker and production
ports.  P0 exposes only an in-memory fake OneBot/provider replay path and uses
the same redaction, observations and sandbox report writer as the CLI.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tests.harness.compare import BaselineStore, canonical_observation, compare_observations
from tests.harness.config import create_run_id, profile_definition
from tests.harness.redact import redact_value
from tests.harness.replay import ReplayEngine
from tests.harness.report import write_run_report
from tests.harness.sandbox import RunSandbox


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_ROUTE_MAP = {"private": "chat", "group_passive": "chat", "group_proactive": "proactive"}
_PROVIDER_TEMPLATES = frozenset({"final", "stream", "tool", "timeout", "cancel", "error"})
_TEMPLATES = frozenset({"plain", "reply", "mention", "merged_forward", "meme"})
_MAX_MESSAGE_COUNT = 10
_MAX_MESSAGE_CHARS = 6_000
_CSRF_PATTERN = re.compile(r'<meta name="xtw-csrf" content="([^"]+)">')


class ConsoleRequestError(ValueError):
    """A browser request is syntactically invalid or outside the local UI schema."""


@dataclass(slots=True)
class ConsoleRun:
    run_id: str
    sandbox: RunSandbox
    created_at: float
    status: str = "READY"
    cancelled: bool = False
    results: list[Any] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    logs: list[dict[str, Any]] = field(default_factory=list)

    def descriptor(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "capture_mode": "COMPLETE",
            "data_source": "P0 in-memory harness + fake provider + fake OneBot",
            "fake_adapters": {"status": "CONNECTED", "capture_mode": "COMPLETE"},
            "astrbot": {"status": "NOT_CONNECTED", "capture_mode": "NOT_CONNECTED"},
            "created_at": self.created_at,
            # The HTTP response is a portable diagnostic, not a filesystem
            # browser. Keep the run's relative location without exposing a
            # workstation path or username.
            "sandbox": "artifacts/test-runs/<run-id>",
            "result_count": len(self.results),
            "cancelled": self.cancelled,
        }


class LocalTestConsole:
    """State holder shared by a deliberately narrow, safe HTTP handler."""

    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        self.session_token = secrets.token_urlsafe(32)
        self._runs: dict[str, ConsoleRun] = {}
        self._run_counter = 0
        self._lock = threading.RLock()

    def create_run(self) -> ConsoleRun:
        with self._lock:
            # ``create_run_id`` is intentionally deterministic to the second for
            # report readability. Keep a console-local suffix so two rapid UI
            # clicks never wait for the next second or overwrite each other.
            self._run_counter += 1
            run_id = f"{create_run_id('ui')}-{self._run_counter:02d}"
            sandbox = RunSandbox.create(self.repository_root, run_id)
            run = ConsoleRun(run_id=run_id, sandbox=sandbox, created_at=time.time())
            self._runs[run_id] = run
            return run

    def list_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [run.descriptor() for run in sorted(self._runs.values(), key=lambda item: item.created_at, reverse=True)]

    def get_run(self, run_id: str) -> ConsoleRun:
        with self._lock:
            try:
                return self._runs[run_id]
            except KeyError as exc:
                raise ConsoleRequestError("unknown run_id") from exc

    @staticmethod
    def _validate_payload(payload: object) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ConsoleRequestError("request body must be a JSON object")
        route = payload.get("route", "private")
        if route not in _ROUTE_MAP:
            raise ConsoleRequestError("route must be private, group_passive or group_proactive")
        provider = payload.get("provider", "final")
        if provider not in _PROVIDER_TEMPLATES:
            raise ConsoleRequestError("unsupported fake provider template")
        template = payload.get("template", "plain")
        if template not in _TEMPLATES:
            raise ConsoleRequestError("unsupported input event template")
        messages = payload.get("messages")
        if messages is None:
            text = payload.get("text")
            messages = [{"text": text, "at_ms": 0}]
        if not isinstance(messages, list) or not 1 <= len(messages) <= _MAX_MESSAGE_COUNT:
            raise ConsoleRequestError("messages must contain one to ten synthetic messages")
        normalized_messages: list[dict[str, Any]] = []
        last_at = -1
        for index, message in enumerate(messages):
            if not isinstance(message, dict) or not isinstance(message.get("text"), str):
                raise ConsoleRequestError(f"messages[{index}] requires text")
            text = message["text"]
            if len(text) > _MAX_MESSAGE_CHARS:
                raise ConsoleRequestError(f"messages[{index}] exceeds {_MAX_MESSAGE_CHARS} characters")
            at_ms = message.get("at_ms", index * int(payload.get("interval_ms", 0)))
            if not isinstance(at_ms, int) or at_ms < 0 or at_ms < last_at:
                raise ConsoleRequestError("message at_ms values must be monotonic non-negative integers")
            last_at = at_ms
            normalized_messages.append({"text": text, "at_ms": at_ms})
        images = payload.get("images", [])
        if not isinstance(images, list) or len(images) > 8:
            raise ConsoleRequestError("images must be a list with at most eight synthetic descriptors")
        normalized_images: list[dict[str, str]] = []
        for index, image in enumerate(images):
            if not isinstance(image, dict):
                raise ConsoleRequestError(f"images[{index}] must be an object")
            image_id = image.get("id", f"synthetic-ui-image-{index + 1}")
            mime = image.get("mime", "image/jpeg")
            if not isinstance(image_id, str) or not image_id.startswith("synthetic-"):
                raise ConsoleRequestError("image IDs must use the synthetic- prefix")
            if mime not in {"image/jpeg", "image/png", "image/webp", "text/plain"}:
                raise ConsoleRequestError("only synthetic JPEG/PNG/WebP/text fixtures are allowed")
            normalized_images.append({"id": image_id, "mime": mime})
        return {"route": route, "provider": provider, "template": template, "messages": normalized_messages, "images": normalized_images}

    def _make_case(self, run: ConsoleRun, payload: dict[str, Any]) -> dict[str, Any]:
        timeline: list[dict[str, Any]] = []
        route = _ROUTE_MAP[payload["route"]]
        group_id = "synthetic-ui-private" if payload["route"] == "private" else "synthetic-ui-group"
        user_id = "synthetic-ui-user"
        for index, message in enumerate(payload["messages"]):
            components: list[dict[str, Any]] = []
            if index == 0:
                for image in payload["images"]:
                    component_type = "mface" if payload["template"] == "meme" else "image"
                    components.append(
                        {"type": component_type, "data": {"image_id": image["id"], "file": f"/synthetic/{image['id']}.bin", "mime": image["mime"]}}
                    )
            if payload["template"] == "mention":
                components.append({"type": "at", "data": {"qq": "synthetic-ui-bot"}})
            components.append({"type": "text", "data": {"text": message["text"]}})
            timeline.append(
                {
                    "op": "event",
                    "at": message["at_ms"] / 1_000,
                    "event": {
                        "message_id": f"synthetic-ui-{run.run_id}-message-{len(run.results) + 1:02d}-{index + 1:02d}",
                        "user_id": user_id,
                        "group_id": group_id,
                        "raw_message": message["text"],
                        "message": components,
                    },
                }
            )
        final_at = payload["messages"][-1]["at_ms"] / 1_000 + 3.0
        timeline.append({"op": "flush", "at": final_at})
        provider = payload["provider"]
        simulation: dict[str, Any] = {"request_roles": ["main_reply"], "audit": "allow", "deliveries": 1}
        if provider == "stream":
            simulation["stream"] = True
        elif provider == "tool":
            simulation["request_roles"] = ["main_reply", "tool_continuation"]
            simulation["tools"] = [{"name": "synthetic_lookup", "effect": "read"}]
        elif provider in {"timeout", "cancel", "error"}:
            simulation["deliveries"] = 0
            simulation["output"] = f"Synthetic provider {provider} path."
        if payload["template"] == "merged_forward":
            simulation["audit"] = "block"
        if payload["images"]:
            simulation["vlm_calls"] = len(payload["images"])
        return {
            "id": f"p0-ui-{run.run_id.replace('_', '-')}-{len(run.results) + 1}",
            "title": "Local Test Console synthetic input",
            "route": route,
            "tags": ["interactive", "local-console"],
            "timeline": timeline,
            "simulation": simulation,
            "expected": {
                "turns_ready": 1,
                "main_reply_requests": 1,
                "deliveries": simulation["deliveries"],
                "security_safe": True,
            },
            "compare": {"text": "structural", "ignore_fields": ["duration_ms", "generated_at"]},
        }

    def submit_input(self, run_id: str, payload: object) -> dict[str, Any]:
        normalized = self._validate_payload(payload)
        with self._lock:
            run = self.get_run(run_id)
            if run.cancelled:
                raise ConsoleRequestError("this run was cancelled")
            case = self._make_case(run, normalized)
            result = ReplayEngine().run_case(case, run_id=f"{run.run_id}-{len(run.results) + 1}")
            run.results.append(result)
            run.events.extend(result.trace)
            run.logs.extend(result.logs)
            run.status = "PASS" if result.passed else "FAIL"
            comparisons = tuple(
                compare_observations(
                    case_id=item.case_id,
                    baseline=BaselineStore(self.repository_root).load(item.case_id),
                    candidate=canonical_observation(item),
                    baseline_name="approved",
                )
                for item in run.results
            )
            write_run_report(
                sandbox=run.sandbox,
                repository_root=self.repository_root,
                profile=profile_definition("quick"),
                replay_results=tuple(run.results),
                comparisons=comparisons,
            )
            return {
                "run": run.descriptor(),
                "result": redact_value(result.to_dict()),
                "capture_mode": "COMPLETE",
                "astrbot_capture_mode": "NOT_CONNECTED",
                "note": "Current synthetic input remains in this local browser session; persisted reports contain redacted structural observations.",
            }

    def cancel(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            run = self.get_run(run_id)
            run.cancelled = True
            run.status = "CANCELLED"
            run.events.append(
                {
                    "schema_version": 1,
                    "sequence": len(run.events) + 1,
                    "at": 0.0,
                    "kind": "turn.cancelled",
                    "source": "LOCAL_TEST_CONSOLE",
                    "run_id": run.run_id,
                    "capture_mode": "COMPLETE",
                    "payload": {"reason": "ui_cancel"},
                }
            )
            return run.descriptor()

    def view(self, run_id: str, section: str) -> Any:
        run = self.get_run(run_id)
        if section == "timeline":
            return list(run.events)
        if section == "requests":
            return [request for result in run.results for request in result.requests]
        if section == "logs":
            return list(run.logs)
        if section == "outputs":
            return [output for result in run.results for output in result.outputs]
        if section == "compare":
            return [
                compare_observations(
                    case_id=result.case_id,
                    baseline=BaselineStore(self.repository_root).load(result.case_id),
                    candidate=canonical_observation(result),
                    baseline_name="approved",
                ).to_dict()
                for result in run.results
            ]
        if section == "run":
            return run.descriptor()
        raise ConsoleRequestError("unknown view section")


def _frontend_root() -> Path:
    return Path(__file__).resolve().parents[1] / "frontend"


def _cookie_value(header: str | None, name: str) -> str | None:
    if not header:
        return None
    for part in header.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key == name:
            return value
    return None


def create_console_server(repository_root: Path, host: str = "127.0.0.1", port: int = 0) -> tuple[ThreadingHTTPServer, LocalTestConsole]:
    """Build, but do not start, a local-only server for tests and CLI usage."""

    if host not in _LOOPBACK_HOSTS:
        raise ConsoleRequestError("Local Test Console may only bind a loopback host")
    state = LocalTestConsole(repository_root)
    frontend_root = _frontend_root()

    class Handler(BaseHTTPRequestHandler):
        server_version = "XiaotianwenLocalTestConsole/0.1"

        def log_message(self, format: str, *args: object) -> None:
            # Do not log query strings, cookies, payloads or browser headers.
            return

        def _origin(self) -> str:
            bound_host, bound_port = self.server.server_address[:2]
            return f"http://{bound_host}:{bound_port}"

        def _authenticated(self, *, write: bool = False) -> bool:
            if _cookie_value(self.headers.get("Cookie"), "xtw_test_session") != state.session_token:
                return False
            if write:
                origin = self.headers.get("Origin")
                csrf = self.headers.get("X-XTW-CSRF")
                return origin == self._origin() and csrf == state.session_token
            return True

        def _send_json(self, status: HTTPStatus, payload: Any) -> None:
            body = json.dumps(redact_value(payload), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _send_text(self, status: HTTPStatus, content_type: str, body: str, *, cookie: bool = False) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; connect-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'")
            if cookie:
                self.send_header("Set-Cookie", f"xtw_test_session={state.session_token}; HttpOnly; SameSite=Strict; Path=/")
            self.end_headers()
            self.wfile.write(encoded)

        def _read_json(self) -> Any:
            length_header = self.headers.get("Content-Length")
            if length_header is None:
                raise ConsoleRequestError("Content-Length is required")
            try:
                length = int(length_header)
            except ValueError as exc:
                raise ConsoleRequestError("invalid Content-Length") from exc
            if length < 0 or length > 128_000:
                raise ConsoleRequestError("request body exceeds local console limit")
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ConsoleRequestError("request body must be UTF-8 JSON") from exc

        def _route(self) -> tuple[list[str], dict[str, list[str]]]:
            parsed = urlparse(self.path)
            if parsed.query:
                raise ConsoleRequestError("query parameters are not accepted by this API")
            return [part for part in parsed.path.split("/") if part], {}

        def do_GET(self) -> None:
            try:
                parts, _ = self._route()
                if parts == []:
                    index = (frontend_root / "index.html").read_text(encoding="utf-8").replace("{{CSRF_TOKEN}}", state.session_token)
                    self._send_text(HTTPStatus.OK, "text/html; charset=utf-8", index, cookie=True)
                    return
                if parts == ["static", "app.js"]:
                    self._send_text(HTTPStatus.OK, "application/javascript; charset=utf-8", (frontend_root / "app.js").read_text(encoding="utf-8"))
                    return
                if parts == ["static", "styles.css"]:
                    self._send_text(HTTPStatus.OK, "text/css; charset=utf-8", (frontend_root / "styles.css").read_text(encoding="utf-8"))
                    return
                if not self._authenticated():
                    self._send_json(HTTPStatus.FORBIDDEN, {"error": "local session required"})
                    return
                if parts == ["api", "runs"]:
                    self._send_json(HTTPStatus.OK, {"runs": state.list_runs(), "capture_mode": "COMPLETE"})
                    return
                if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "stream":
                    run = state.get_run(parts[2])
                    lines = []
                    for event in run.events:
                        lines.append(f"id: {event.get('sequence', 0)}\nevent: trace\ndata: {json.dumps(redact_value(event), ensure_ascii=False)}\n")
                    lines.append("event: complete\ndata: {}\n")
                    encoded = "\n".join(lines).encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(encoded)
                    return
                if len(parts) == 4 and parts[:2] == ["api", "runs"]:
                    self._send_json(HTTPStatus.OK, state.view(parts[2], parts[3]))
                    return
                if len(parts) == 3 and parts[:2] == ["api", "runs"]:
                    self._send_json(HTTPStatus.OK, state.view(parts[2], "run"))
                    return
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            except ConsoleRequestError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        def do_POST(self) -> None:
            try:
                parts, _ = self._route()
                if not self._authenticated(write=True):
                    self._send_json(HTTPStatus.FORBIDDEN, {"error": "valid local session, origin and CSRF header required"})
                    return
                if parts == ["api", "runs"]:
                    run = state.create_run()
                    self._send_json(HTTPStatus.CREATED, {"run": run.descriptor()})
                    return
                if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "inputs":
                    self._send_json(HTTPStatus.OK, state.submit_input(parts[2], self._read_json()))
                    return
                if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "cancel":
                    self._send_json(HTTPStatus.OK, {"run": state.cancel(parts[2])})
                    return
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            except ConsoleRequestError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    return ThreadingHTTPServer((host, port), Handler), state


def run_console(
    *,
    repository_root: Path,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
    duration_seconds: float | None = None,
) -> int:
    """Start the console without touching any existing service or production port."""

    server, state = create_console_server(repository_root, host=host, port=port)
    bound_host, bound_port = server.server_address[:2]
    url = f"http://{bound_host}:{bound_port}/"
    print(f"Local Test Console: {url}")
    print("Mode: P0 offline in-memory harness; capture=COMPLETE; no AstrBot/SnowLuma process is connected.")
    if open_browser:
        webbrowser.open(url, new=1)
    if duration_seconds is not None:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        worker.join(timeout=max(0.0, duration_seconds))
        server.shutdown()
        server.server_close()
        return 0
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Local Test Console stopped.")
    finally:
        server.shutdown()
        server.server_close()
    return 0
