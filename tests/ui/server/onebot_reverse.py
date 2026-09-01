"""Small stdlib-only OneBot v11 reverse WebSocket bridge for the local console.

The bridge is deliberately a client of AstrBot's local ``/ws`` endpoint. It
does not connect to QQ, SnowLuma, a provider, or any non-loopback address. It
is used only by an explicitly enabled disposable local test profile.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_MAX_FRAME_BYTES = 2_000_000


class OneBotBridgeError(RuntimeError):
    """The local reverse WebSocket bridge could not connect or send."""


def _validate_ws_url(url: str) -> tuple[str, int, str]:
    parsed = urlparse(url)
    if parsed.scheme != "ws" or parsed.hostname not in _LOOPBACK_HOSTS:
        raise OneBotBridgeError("OneBot bridge only accepts a loopback ws:// URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise OneBotBridgeError("OneBot bridge URL must not contain credentials or query data")
    port = parsed.port or 80
    path = parsed.path or "/ws"
    if path.rstrip("/") not in {"/ws", "/ws/event", "/ws/api"}:
        raise OneBotBridgeError("OneBot bridge URL must use AstrBot's /ws endpoint")
    return parsed.hostname, port, path


def _read_exact(sock: socket.socket, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise OneBotBridgeError("AstrBot reverse WebSocket closed the connection")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _client_frame(payload: bytes, *, opcode: int = 1) -> bytes:
    if len(payload) > _MAX_FRAME_BYTES:
        raise OneBotBridgeError("OneBot bridge frame exceeds the local size limit")
    mask = secrets.token_bytes(4)
    masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    first = 0x80 | (opcode & 0x0F)
    length = len(masked)
    if length < 126:
        header = bytes((first, 0x80 | length))
    elif length <= 0xFFFF:
        header = bytes((first, 0x80 | 126)) + struct.pack("!H", length)
    else:
        header = bytes((first, 0x80 | 127)) + struct.pack("!Q", length)
    return header + mask + masked


def _server_frame(sock: socket.socket) -> tuple[int, bool, bytes]:
    first, second = _read_exact(sock, 2)
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length_code = second & 0x7F
    if length_code == 126:
        length = struct.unpack("!H", _read_exact(sock, 2))[0]
    elif length_code == 127:
        length = struct.unpack("!Q", _read_exact(sock, 8))[0]
    else:
        length = length_code
    if length > _MAX_FRAME_BYTES:
        raise OneBotBridgeError("AstrBot reverse WebSocket frame exceeds the local size limit")
    mask = _read_exact(sock, 4) if masked else None
    payload = _read_exact(sock, length)
    if mask:
        payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return opcode, fin, payload


class OneBotReverseBridge:
    """A bounded local OneBot v11 reverse WebSocket client.

    AstrBot owns the WebSocket server. The bridge sends synthetic OneBot
    events and acknowledges outbound OneBot actions locally, so no action can
    reach a real QQ transport.
    """

    def __init__(
        self,
        *,
        url: str,
        token: str,
        self_id: str = "1000000001",
        on_payload: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        host, port, path = _validate_ws_url(url)
        if not token or any(character.isspace() for character in token):
            raise OneBotBridgeError("OneBot bridge requires a non-empty local token")
        if not self_id or not self_id.isdigit():
            raise OneBotBridgeError("OneBot bridge self_id must be a numeric synthetic ID")
        self.url = f"ws://{host}:{port}{path}"
        self._host = host
        self._port = port
        self._path = path
        self._token = token
        self._self_id = self_id
        self._on_payload = on_payload
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: socket.socket | None = None
        self._send_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._status = "NOT_CONNECTED"
        self._last_error: str | None = None
        self._connected_at: float | None = None
        self._last_activity: float | None = None
        self._sent_events = 0
        self._received_actions = 0
        self._auto_replies = 0
        self._event_to_run: dict[str, str] = {}
        self._active_run_id: str | None = None
        self._outbound_message_id = 1

    @property
    def self_id(self) -> str:
        return self._self_id

    def start(self) -> None:
        with self._state_lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="xtw-onebot-bridge", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._close_socket()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._state_lock:
            self._status = "NOT_CONNECTED"

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            status = self._status
            return {
                "status": status,
                "capture_mode": "COMPLETE" if status == "CONNECTED" else "NOT_CONNECTED",
                "endpoint": self.url,
                "scope": "local_onebot_reverse_websocket_only",
                "self_id": self._self_id,
                "sent_events": self._sent_events,
                "received_actions": self._received_actions,
                "auto_replies": self._auto_replies,
                "connected_at": self._connected_at,
                "last_activity": self._last_activity,
                "last_error": self._last_error,
                "note": (
                    "测试台已替代 SnowLuma：仅发送合成 OneBot 事件并在本地确认 AstrBot action。"
                    if status == "CONNECTED"
                    else "OneBot 反向连接尚未建立；不会向 QQ 或 SnowLuma 发消息。"
                ),
            }

    def register_event(self, event_id: str | int, run_id: str) -> None:
        with self._state_lock:
            self._event_to_run[str(event_id)] = run_id
            self._active_run_id = run_id

    def run_for_payload(self, payload: dict[str, Any]) -> str | None:
        context = payload.get("params")
        if isinstance(context, dict):
            context = context.get("context")
            if isinstance(context, dict):
                event_id = context.get("message_id")
                if event_id is not None:
                    with self._state_lock:
                        found = self._event_to_run.get(str(event_id))
                        if found:
                            return found
        with self._state_lock:
            return self._active_run_id

    def send_event(self, event: dict[str, Any], *, run_id: str) -> None:
        event_id = event.get("message_id")
        if event_id is None:
            raise OneBotBridgeError("OneBot event requires message_id")
        self.register_event(event_id, run_id)
        self._send_json(event)
        with self._state_lock:
            self._sent_events += 1
            self._last_activity = time.time()

    def _set_status(self, status: str, error: str | None = None) -> None:
        with self._state_lock:
            self._status = status
            self._last_error = error
            if status == "CONNECTED":
                self._connected_at = time.time()
            elif status != "CONNECTED":
                self._connected_at = None

    def _run(self) -> None:
        while not self._stop.is_set():
            self._set_status("CONNECTING")
            try:
                sock = self._connect()
                with self._state_lock:
                    self._socket = sock
                self._set_status("CONNECTED")
                self._receive_loop(sock)
            except Exception as exc:  # the UI receives the bounded error text
                self._set_status("NOT_CONNECTED", f"{type(exc).__name__}: {exc}")
            finally:
                self._close_socket()
            if not self._stop.wait(1.5):
                continue

    def _connect(self) -> socket.socket:
        sock = socket.create_connection((self._host, self._port), timeout=5.0)
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        headers = [
            f"GET {self._path} HTTP/1.1",
            f"Host: {self._host}:{self._port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
            f"X-Self-ID: {self._self_id}",
            "X-Client-Role: universal",
            f"Authorization: Bearer {self._token}",
            "User-Agent: XiaotianwenLocalTestConsole/onebot-bridge",
            "",
            "",
        ]
        sock.sendall("\r\n".join(headers).encode("ascii"))
        response = bytearray()
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise OneBotBridgeError("AstrBot closed the reverse WebSocket handshake")
            response.extend(chunk)
            if len(response) > 64_000:
                raise OneBotBridgeError("AstrBot reverse WebSocket handshake is too large")
        header_block = bytes(response).split(b"\r\n\r\n", 1)[0].decode("iso-8859-1")
        lines = header_block.split("\r\n")
        if not lines or " 101 " not in f" {lines[0]} ":
            raise OneBotBridgeError(f"AstrBot reverse WebSocket handshake failed: {lines[0] if lines else 'empty response'}")
        response_headers = {}
        for line in lines[1:]:
            name, separator, value = line.partition(":")
            if separator:
                response_headers[name.strip().lower()] = value.strip()
        expected = base64.b64encode(hashlib.sha1((key + _WEBSOCKET_GUID).encode("ascii")).digest()).decode("ascii")
        if response_headers.get("sec-websocket-accept") != expected:
            raise OneBotBridgeError("AstrBot reverse WebSocket handshake accept value did not match")
        sock.settimeout(None)
        return sock

    def _receive_loop(self, sock: socket.socket) -> None:
        fragments: list[bytes] = []
        fragment_opcode: int | None = None
        while not self._stop.is_set():
            opcode, fin, payload = _server_frame(sock)
            if opcode == 8:
                return
            if opcode == 9:
                self._send_frame(payload, opcode=10)
                continue
            if opcode == 10:
                continue
            if opcode in {1, 2}:
                if fin:
                    self._handle_message(payload)
                else:
                    fragments = [payload]
                    fragment_opcode = opcode
                continue
            if opcode == 0 and fragment_opcode is not None:
                fragments.append(payload)
                if fin:
                    self._handle_message(b"".join(fragments))
                    fragments = []
                    fragment_opcode = None

    def _handle_message(self, raw: bytes) -> None:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        with self._state_lock:
            self._last_activity = time.time()
            if "action" in payload:
                self._received_actions += 1
        if self._on_payload:
            try:
                self._on_payload(payload)
            except Exception:
                # Observability must not tear down the AstrBot transport.
                pass
        if "action" in payload:
            self._send_json(self._response_for_action(payload))
            with self._state_lock:
                self._auto_replies += 1

    def _response_for_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action", ""))
        if action == "get_login_info":
            data: Any = {"user_id": int(self._self_id), "nickname": "xtw-test-bot"}
        elif action == "get_status":
            data = {"online": True, "good": True}
        elif action == "get_version_info":
            data = {"app_name": "xiaotianwen-local-test-console", "app_version": "0.1"}
        elif action in {"send_msg", "send_private_msg", "send_group_msg", "send_forward_msg", "send_group_forward_msg"} or action.startswith("send_"):
            with self._state_lock:
                message_id = self._outbound_message_id
                self._outbound_message_id += 1
            data = {"message_id": message_id}
        elif action in {"can_send_image", "can_send_record", "can_send_video"}:
            data = {"yes": False}
        else:
            # The test transport acknowledges read-style and plugin-specific
            # actions without pretending to provide real QQ data.
            data = {}
        response: dict[str, Any] = {"status": "ok", "retcode": 0, "data": data}
        if "echo" in payload:
            response["echo"] = payload["echo"]
        return response

    def _send_json(self, payload: dict[str, Any]) -> None:
        self._send_frame(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def _send_frame(self, payload: bytes, *, opcode: int = 1) -> None:
        with self._send_lock:
            sock = self._socket
            if sock is None:
                raise OneBotBridgeError("AstrBot reverse WebSocket is not connected")
            sock.sendall(_client_frame(payload, opcode=opcode))

    def _close_socket(self) -> None:
        with self._send_lock:
            sock = self._socket
            self._socket = None
            if sock is None:
                return
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass


def load_onebot_bridge_settings(data_dir: str | Path) -> dict[str, str]:
    """Read only the local aiocqhttp connection fields from a data copy."""

    root = Path(data_dir).expanduser().resolve()
    config_path = root / "cmd_config.json"
    # AstrBot writes cmd_config.json with a UTF-8 BOM on some Windows setups.
    # Accept both BOM and BOM-less copies without changing the source data.
    with config_path.open("r", encoding="utf-8-sig") as handle:
        config = json.load(handle)
    platforms = config.get("platform")
    if not isinstance(platforms, list):
        raise OneBotBridgeError("AstrBot data copy has no platform list")
    for platform in platforms:
        if not isinstance(platform, dict) or platform.get("type") != "aiocqhttp":
            continue
        port = platform.get("ws_reverse_port")
        token = platform.get("ws_reverse_token")
        if not isinstance(port, int) or not isinstance(token, str):
            raise OneBotBridgeError("AstrBot data copy has incomplete aiocqhttp reverse settings")
        return {
            "url": f"ws://127.0.0.1:{port}/ws",
            "token": token,
        }
    raise OneBotBridgeError("AstrBot data copy has no aiocqhttp platform")
