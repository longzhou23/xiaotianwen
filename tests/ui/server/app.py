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
from tests.ui.server.astrbot_connector import LocalAstrBotConnector
from tests.ui.server.onebot_reverse import OneBotBridgeError, OneBotReverseBridge, load_onebot_bridge_settings


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_ROUTE_MAP = {"private": "chat", "group_passive": "chat", "group_proactive": "proactive"}
_PROVIDER_TEMPLATES = frozenset({"final", "stream", "tool", "timeout", "cancel", "error"})
_TEMPLATES = frozenset({"plain", "reply", "mention", "merged_forward", "meme"})
_MAX_MESSAGE_COUNT = 10
_MAX_MESSAGE_CHARS = 6_000
_CSRF_PATTERN = re.compile(r'<meta name="xtw-csrf" content="([^"]+)">')
_EXECUTION_MODES = frozenset({"fake", "astrbot"})


class ConsoleRequestError(ValueError):
    """A browser request is syntactically invalid or outside the local UI schema."""


@dataclass(slots=True)
class ConsoleRun:
    run_id: str
    sandbox: RunSandbox
    created_at: float
    status: str = "READY"
    cancelled: bool = False
    execution_mode: str = "fake"
    results: list[Any] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    logs: list[dict[str, Any]] = field(default_factory=list)
    live_requests: list[dict[str, Any]] = field(default_factory=list)
    live_outputs: list[dict[str, Any]] = field(default_factory=list)
    live_events: list[dict[str, Any]] = field(default_factory=list)
    live_logs: list[dict[str, Any]] = field(default_factory=list)
    live_condition: threading.Condition = field(default_factory=threading.Condition, repr=False)
    live_last_activity: float = 0.0

    def descriptor(self, astrbot: dict[str, Any] | None = None) -> dict[str, Any]:
        live = self.execution_mode == "astrbot"
        live_capture = "COMPLETE" if self.live_outputs else "PARTIAL"
        return {
            "run_id": self.run_id,
            "status": self.status,
            "capture_mode": live_capture if live else "COMPLETE",
            "execution_mode": self.execution_mode,
            "data_source": (
                "local AstrBot + Local Test Console OneBot bridge; SnowLuma not running"
                if live
                else "P0 in-memory harness + fake provider + fake OneBot; local AstrBot observer"
            ),
            "fake_adapters": (
                {"status": "NOT_USED", "capture_mode": "NOT_CONNECTED"}
                if live
                else {"status": "CONNECTED", "capture_mode": "COMPLETE"}
            ),
            "astrbot": astrbot or {"status": "NOT_CONNECTED", "capture_mode": "NOT_CONNECTED"},
            "created_at": self.created_at,
            # The HTTP response is a portable diagnostic, not a filesystem
            # browser. Keep the run's relative location without exposing a
            # workstation path or username.
            "sandbox": "artifacts/test-runs/<run-id>",
            "result_count": len(self.results) + (1 if live and (self.live_requests or self.live_outputs) else 0),
            "cancelled": self.cancelled,
        }


class LocalTestConsole:
    """State holder shared by a deliberately narrow, safe HTTP handler."""

    def __init__(
        self,
        repository_root: Path,
        *,
        astrbot_url: str | None = None,
        astrbot_data_dir: str | Path | None = None,
        live_astrbot: bool = False,
        onebot_ws_url: str | None = None,
        onebot_token: str | None = None,
        onebot_self_id: str = "1000000001",
        live_timeout_seconds: float = 45.0,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.session_token = secrets.token_urlsafe(32)
        self.astrbot = LocalAstrBotConnector(
            self.repository_root,
            endpoint=astrbot_url,
            data_dir=astrbot_data_dir,
        )
        self._runs: dict[str, ConsoleRun] = {}
        self._run_counter = 0
        self._lock = threading.RLock()
        self.live_astrbot = live_astrbot
        self.live_timeout_seconds = max(5.0, min(float(live_timeout_seconds), 120.0))
        self.onebot_bridge: OneBotReverseBridge | None = None
        if live_astrbot:
            if not astrbot_data_dir and not onebot_ws_url:
                raise ConsoleRequestError("live AstrBot mode requires --astrbot-data-dir or --onebot-ws-url")
            settings = load_onebot_bridge_settings(astrbot_data_dir) if astrbot_data_dir else {"url": "", "token": ""}
            bridge_url = onebot_ws_url or settings["url"]
            bridge_token = onebot_token if onebot_token is not None else settings["token"]
            self.onebot_bridge = OneBotReverseBridge(
                url=bridge_url,
                token=bridge_token,
                self_id=onebot_self_id,
                on_payload=self._on_live_payload,
            )
            self.onebot_bridge.start()

    def create_run(self) -> ConsoleRun:
        with self._lock:
            # ``create_run_id`` is intentionally deterministic to the second for
            # report readability. Keep a console-local suffix so two rapid UI
            # clicks never wait for the next second or overwrite each other.
            self._run_counter += 1
            run_id = f"{create_run_id('ui')}-{self._run_counter:02d}"
            sandbox = RunSandbox.create(self.repository_root, run_id)
            run = ConsoleRun(
                run_id=run_id,
                sandbox=sandbox,
                created_at=time.time(),
                execution_mode="astrbot" if self.live_astrbot else "fake",
            )
            self._runs[run_id] = run
            return run

    def list_runs(self) -> list[dict[str, Any]]:
        with self._lock:
            astrbot = self.astrbot_descriptor()
            return [
                run.descriptor(astrbot)
                for run in sorted(self._runs.values(), key=lambda item: item.created_at, reverse=True)
            ]

    def astrbot_snapshot(self) -> dict[str, Any]:
        snapshot = self.astrbot.snapshot()
        if self.onebot_bridge:
            snapshot = dict(snapshot)
            snapshot["onebot_bridge"] = self.onebot_bridge.snapshot()
            snapshot["dispatch"] = "ENABLED" if snapshot["onebot_bridge"]["status"] == "CONNECTED" else "DISABLED"
            snapshot["note"] = (
                "本地 OneBot 反向桥已连接：测试台替代 SnowLuma，只接收合成事件并在本地确认 AstrBot action。"
                if snapshot["onebot_bridge"]["status"] == "CONNECTED"
                else "测试台正在等待本地 OneBot 反向桥；不会向 QQ 或 SnowLuma 发消息。"
            )
        return snapshot

    def astrbot_descriptor(self) -> dict[str, Any]:
        snapshot = self.astrbot_snapshot()
        descriptor = {
            key: snapshot[key]
            for key in ("status", "capture_mode", "instance", "endpoint", "scope", "hooks", "dispatch", "note")
        }
        if self.onebot_bridge:
            descriptor["onebot_bridge"] = snapshot["onebot_bridge"]
            descriptor["dispatch"] = snapshot["dispatch"]
            descriptor["note"] = snapshot["note"]
        return descriptor

    def close(self) -> None:
        if self.onebot_bridge:
            self.onebot_bridge.stop()

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
        execution = payload.get("execution", "fake")
        if execution not in _EXECUTION_MODES:
            raise ConsoleRequestError("execution must be fake or astrbot")
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
        return {
            "execution": execution,
            "route": route,
            "provider": provider,
            "template": template,
            "messages": normalized_messages,
            "images": normalized_images,
        }

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

    @staticmethod
    def _make_live_event(run: ConsoleRun, payload: dict[str, Any], index: int, message: dict[str, Any]) -> dict[str, Any]:
        if payload["template"] != "plain" or payload["images"]:
            raise ConsoleRequestError("本地 AstrBot 路径当前只支持普通文本；图片和特殊消息仍请使用 Fake harness")
        is_group = payload["route"] != "private"
        event_id = f"synthetic-live-{run.run_id}-{index + 1:02d}"
        event: dict[str, Any] = {
            "time": int(time.time()),
            "self_id": "1000000001",
            "post_type": "message",
            "message_type": "group" if is_group else "private",
            "sub_type": "normal",
            "message_id": event_id,
            "user_id": "1000000002",
            "message": [{"type": "text", "data": {"text": message["text"]}}],
            "raw_message": message["text"],
            "font": 0,
            "sender": {
                "user_id": "1000000002",
                "nickname": "xtw-local-test-user",
                "card": "xtw-local-test-user",
            },
        }
        if is_group:
            event["group_id"] = "1000000003"
            event["sender"]["role"] = "member"
        return event

    @staticmethod
    def _live_event_shape(event: dict[str, Any]) -> dict[str, Any]:
        return {
            "message_id": event.get("message_id"),
            "post_type": event.get("post_type"),
            "message_type": event.get("message_type"),
            "route": "group" if event.get("group_id") else "private",
            "segment_types": [str(item.get("type")) for item in event.get("message", []) if isinstance(item, dict)],
            "text_chars": len(str(event.get("raw_message", ""))),
            "capture_mode": "COMPLETE",
        }

    @staticmethod
    def _live_action_shape(payload: dict[str, Any]) -> dict[str, Any]:
        params = payload.get("params")
        params = params if isinstance(params, dict) else {}
        message = params.get("message")
        operation = params.get("operation")
        if isinstance(operation, dict):
            message = operation.get("reply", message)
        segments = message if isinstance(message, list) else []
        return {
            "action": str(payload.get("action", "")),
            "param_keys": sorted(str(key) for key in params),
            "segment_types": [str(item.get("type")) for item in segments if isinstance(item, dict)],
            "text_chars": sum(
                len(str(item.get("data", {}).get("text", "")))
                for item in segments
                if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("data"), dict)
            ),
            "capture_mode": "COMPLETE",
        }

    @staticmethod
    def _live_output_error(output: dict[str, Any]) -> str | None:
        """Classify provider failures that AstrBot sends as a normal text action."""

        payload = output.get("payload")
        if not isinstance(payload, dict):
            return None
        message = payload.get("message")
        operation = payload.get("operation")
        if isinstance(operation, dict):
            message = operation.get("reply", message)
        segments = message if isinstance(message, list) else [message]
        text = "".join(
            str(item.get("data", {}).get("text", ""))
            for item in segments
            if isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("data"), dict)
        )
        if not text:
            return None
        if text.lstrip().startswith(("LLM 响应错误:", "LLM response error:")) or any(
            marker in text
            for marker in ("All chat models failed:", "TransportAuthError:", "ProviderError:")
        ):
            return "AstrBot 返回了错误输出；请查看 Output Inspector 中的原始错误信息"
        return None

    def _live_failure_reason(self, run: ConsoleRun) -> str | None:
        for output in run.live_outputs:
            reason = self._live_output_error(output)
            if reason:
                return reason
        return None

    def _on_live_payload(self, payload: dict[str, Any]) -> None:
        bridge = self.onebot_bridge
        if bridge is None:
            return
        run_id = bridge.run_for_payload(payload)
        if not run_id:
            return
        try:
            run = self.get_run(run_id)
        except ConsoleRequestError:
            return
        action = payload.get("action")
        with self._lock:
            event_number = len(run.live_events) + 1
            if action:
                shape = self._live_action_shape(payload)
                request = {
                    "request_id": f"{run.run_id}-astrbot-action-{event_number:03d}",
                    "source": "ASTRBOT",
                    "kind": "onebot.action",
                    "action": shape["action"],
                    "params": payload.get("params", {}),
                    "shape": shape,
                    "capture_mode": "COMPLETE",
                }
                run.live_requests.append(request)
                if shape["action"] == ".handle_quick_operation_async" or shape["action"].startswith("send_"):
                    run.live_outputs.append(
                        {
                            "source": "ASTRBOT",
                            "kind": "delivery.captured",
                            "action": shape["action"],
                            "payload": payload.get("params", {}),
                            "delivery": "CAPTURED_BY_TEST_CONSOLE",
                            "sent_to_qq": False,
                            "capture_mode": "COMPLETE",
                        }
                    )
                run.live_logs.append(
                    {
                        "source": "ASTRBOT",
                        "level": "INFO",
                        "kind": "onebot.action",
                        "message": f"captured local AstrBot action: {shape['action']}",
                        "request_id": request["request_id"],
                        "capture_mode": "COMPLETE",
                    }
                )
                run.live_events.append(
                    {
                        "schema_version": 1,
                        "sequence": event_number,
                        "at": time.time(),
                        "kind": "astrbot.action.captured",
                        "source": "ASTRBOT",
                        "run_id": run.run_id,
                        "request_id": request["request_id"],
                        "payload": shape,
                        "capture_mode": "COMPLETE",
                    }
                )
            run.live_last_activity = time.monotonic()
        with run.live_condition:
            run.live_condition.notify_all()

    def _live_report(self, run: ConsoleRun, payload: dict[str, Any]) -> dict[str, Any]:
        failure_reason = self._live_failure_reason(run)
        report = {
            "schema_version": 1,
            "execution_mode": "astrbot",
            "input": [self._live_event_shape(item) for item in payload["events"]],
            "requests": [item["shape"] for item in run.live_requests],
            "outputs": [
                {
                    "action": item.get("action"),
                    "delivery": item.get("delivery"),
                    "sent_to_qq": False,
                    "capture_mode": item.get("capture_mode", "COMPLETE"),
                }
                for item in run.live_outputs
            ],
            "event_count": len(run.live_events),
            "request_count": len(run.live_requests),
            "output_count": len(run.live_outputs),
            "passed": bool(run.live_outputs) and failure_reason is None,
            "failure_reason": failure_reason,
            "capture_mode": run.descriptor(self.astrbot_descriptor())["capture_mode"],
        }
        run.sandbox.write_json("observations/live-astrbot.json", report)
        run.sandbox.write_json("logs/live-astrbot.json", {"events": run.live_logs})
        return report

    def _submit_live_input(self, run: ConsoleRun, normalized: dict[str, Any]) -> dict[str, Any]:
        bridge = self.onebot_bridge
        if bridge is None:
            raise ConsoleRequestError("本地 AstrBot 路径未启用；请用 --live-astrbot 启动测试台")
        if bridge.snapshot()["status"] != "CONNECTED":
            raise ConsoleRequestError("本地 AstrBot OneBot 反向连接尚未建立")
        events = [
            self._make_live_event(run, normalized, index, message)
            for index, message in enumerate(normalized["messages"])
        ]
        with self._lock:
            if run.cancelled:
                raise ConsoleRequestError("this run was cancelled")
            run.execution_mode = "astrbot"
            run.status = "RUNNING"
            run.live_last_activity = time.monotonic()
        for event in events:
            with self._lock:
                run.live_events.append(
                    {
                        "schema_version": 1,
                        "sequence": len(run.live_events) + 1,
                        "at": time.time(),
                        "kind": "onebot.event.sent",
                        "source": "LOCAL_TEST_CONSOLE",
                        "run_id": run.run_id,
                        "payload": self._live_event_shape(event),
                        "capture_mode": "COMPLETE",
                    }
                )
            try:
                bridge.send_event(event, run_id=run.run_id)
            except OneBotBridgeError as exc:
                with self._lock:
                    run.status = "FAIL"
                    run.live_logs.append(
                        {
                            "source": "LOCAL_TEST_CONSOLE",
                            "level": "ERROR",
                            "kind": "onebot.event.failed",
                            "message": str(exc),
                            "capture_mode": "PARTIAL",
                        }
                    )
                raise ConsoleRequestError(str(exc)) from exc
        deadline = time.monotonic() + self.live_timeout_seconds
        with run.live_condition:
            while True:
                now = time.monotonic()
                if now >= deadline:
                    break
                quiet_for = now - run.live_last_activity
                if run.live_requests and quiet_for >= 2.0:
                    break
                wait_for = min(deadline - now, max(0.1, 2.0 - quiet_for))
                run.live_condition.wait(timeout=wait_for)
        with self._lock:
            failure_reason = self._live_failure_reason(run)
            if run.cancelled:
                run.status = "CANCELLED"
            elif failure_reason:
                run.status = "FAIL"
            elif run.live_outputs:
                run.status = "PASS"
            else:
                run.status = "NOT_VERIFIED"
            report = self._live_report(run, {"events": events})
            descriptor = run.descriptor(self.astrbot_descriptor())
            return {
                "run": descriptor,
                "result": {
                    "case_id": f"live-{run.run_id}",
                    "passed": bool(run.live_outputs) and failure_reason is None,
                    "requests": run.live_requests,
                    "outputs": run.live_outputs,
                    "trace": run.live_events,
                    "logs": run.live_logs,
                    "validation_errors": (
                        [failure_reason]
                        if failure_reason
                        else []
                        if run.live_outputs
                        else ["AstrBot did not emit a captured output before the bounded wait ended"]
                    ),
                },
                "capture_mode": descriptor["capture_mode"],
                "astrbot_capture_mode": descriptor["astrbot"]["capture_mode"],
                "note": (
                    "本地 AstrBot action 已被测试台捕获，但内容是错误输出；所有 output 仍只在测试台保存，不会发送到 QQ。"
                    if failure_reason
                    else "本地 AstrBot 已处理合成 OneBot 事件；测试台确认了 AstrBot action，但所有 output 仅在测试台捕获，不会发送到 QQ。"
                ),
                "report": report,
            }

    def submit_input(self, run_id: str, payload: object) -> dict[str, Any]:
        normalized = self._validate_payload(payload)
        run = self.get_run(run_id)
        if normalized["execution"] == "astrbot":
            return self._submit_live_input(run, normalized)
        with self._lock:
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
            astrbot = self.astrbot_descriptor()
            return {
                "run": run.descriptor(astrbot),
                "result": redact_value(result.to_dict()),
                "capture_mode": "COMPLETE",
                "astrbot_capture_mode": astrbot["capture_mode"],
                "note": "本次输入仍由 Fake harness 处理；AstrBot 仅只读观察，不会收到测试消息。持久化报告只保存脱敏结构观测。",
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
            return run.descriptor(self.astrbot_descriptor())

    def view(self, run_id: str, section: str) -> Any:
        run = self.get_run(run_id)
        if section == "timeline":
            return list(run.events) + list(run.live_events)
        if section == "requests":
            return [request for result in run.results for request in result.requests] + list(run.live_requests)
        if section == "logs":
            return list(run.logs) + list(run.live_logs)
        if section == "outputs":
            return [output for result in run.results for output in result.outputs] + list(run.live_outputs)
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
            return run.descriptor(self.astrbot_descriptor())
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


def create_console_server(
    repository_root: Path,
    host: str = "127.0.0.1",
    port: int = 0,
    *,
    astrbot_url: str | None = None,
    astrbot_data_dir: str | Path | None = None,
    live_astrbot: bool = False,
    onebot_ws_url: str | None = None,
    onebot_token: str | None = None,
    onebot_self_id: str = "1000000001",
    live_timeout_seconds: float = 45.0,
) -> tuple[ThreadingHTTPServer, LocalTestConsole]:
    """Build, but do not start, a local-only server for tests and CLI usage."""

    if host not in _LOOPBACK_HOSTS:
        raise ConsoleRequestError("Local Test Console may only bind a loopback host")
    state = LocalTestConsole(
        repository_root,
        astrbot_url=astrbot_url,
        astrbot_data_dir=astrbot_data_dir,
        live_astrbot=live_astrbot,
        onebot_ws_url=onebot_ws_url,
        onebot_token=onebot_token,
        onebot_self_id=onebot_self_id,
        live_timeout_seconds=live_timeout_seconds,
    )
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
                    index = (frontend_root / "index.html").read_text(encoding="utf-8")
                    index = index.replace("{{CSRF_TOKEN}}", state.session_token)
                    index = index.replace("{{DEFAULT_EXECUTION}}", "astrbot" if state.live_astrbot else "fake")
                    index = index.replace("{{FAKE_EXECUTION_SELECTED}}", "" if state.live_astrbot else "selected")
                    index = index.replace("{{ASTRBOT_EXECUTION_SELECTED}}", "selected" if state.live_astrbot else "")
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
                if parts == ["api", "astrbot"]:
                    self._send_json(HTTPStatus.OK, state.astrbot_snapshot())
                    return
                if parts == ["api", "runs"]:
                    self._send_json(
                        HTTPStatus.OK,
                        {
                            "runs": state.list_runs(),
                            "capture_mode": "COMPLETE",
                            "astrbot": state.astrbot_descriptor(),
                        },
                    )
                    return
                if len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "stream":
                    run = state.get_run(parts[2])
                    lines = []
                    for event in list(run.events) + list(run.live_events):
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
                    self._send_json(HTTPStatus.CREATED, {"run": run.descriptor(state.astrbot_descriptor())})
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
    astrbot_url: str | None = None,
    astrbot_data_dir: str | Path | None = None,
    live_astrbot: bool = False,
    onebot_ws_url: str | None = None,
    onebot_token: str | None = None,
    onebot_self_id: str = "1000000001",
    live_timeout_seconds: float = 45.0,
) -> int:
    """Start the console without touching any existing service or production port."""

    server, state = create_console_server(
        repository_root,
        host=host,
        port=port,
        astrbot_url=astrbot_url,
        astrbot_data_dir=astrbot_data_dir,
        live_astrbot=live_astrbot,
        onebot_ws_url=onebot_ws_url,
        onebot_token=onebot_token,
        onebot_self_id=onebot_self_id,
        live_timeout_seconds=live_timeout_seconds,
    )
    bound_host, bound_port = server.server_address[:2]
    url = f"http://{bound_host}:{bound_port}/"
    print(f"Local Test Console: {url}")
    print(
        "Mode: P0 offline in-memory harness + read-only local AstrBot observer; "
        f"AstrBot={state.astrbot_descriptor()['status']} capture={state.astrbot_descriptor()['capture_mode']} "
        f"live_bridge={'enabled' if state.live_astrbot else 'disabled'}."
    )
    if open_browser:
        webbrowser.open(url, new=1)
    if duration_seconds is not None:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        worker.join(timeout=max(0.0, duration_seconds))
        state.close()
        server.shutdown()
        server.server_close()
        return 0
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Local Test Console stopped.")
    finally:
        state.close()
        server.shutdown()
        server.server_close()
    return 0
