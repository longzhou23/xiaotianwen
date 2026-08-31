"""Fail-closed network guard used by P0 replay and child test processes.

It intentionally permits loopback only. A P0 test must inject a fake adapter
instead of relying on an unavailable real endpoint.
"""

from __future__ import annotations

import ipaddress
import socket
import threading
from dataclasses import dataclass
from typing import Any, Callable


class NetworkViolation(RuntimeError):
    """An undeclared socket or DNS operation was attempted by an offline test."""

    exit_code = 3

    def __init__(self, operation: str, target: object) -> None:
        super().__init__(f"offline network guard blocked {operation} to {self._safe_target(target)}")
        self.operation = operation
        self.target = self._safe_target(target)

    @staticmethod
    def _safe_target(target: object) -> str:
        if isinstance(target, tuple):
            host = str(target[0]) if target else "<unknown>"
            port = str(target[1]) if len(target) > 1 else ""
            return f"{host}:{port}"[:160]
        return str(target)[:160]


@dataclass(frozen=True, slots=True)
class NetworkAttempt:
    operation: str
    target: str


def _is_loopback_host(host: object) -> bool:
    if not isinstance(host, str):
        return False
    normalized = host.strip().strip("[]").lower()
    if normalized in {"localhost", "ip6-localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _host_from_address(address: object) -> object:
    if isinstance(address, tuple) and address:
        return address[0]
    return address


class NetworkGuard:
    """Temporarily prohibit external name resolution and socket communication."""

    def __init__(self, *, allow_loopback: bool = True) -> None:
        self.allow_loopback = allow_loopback
        self.attempts: list[NetworkAttempt] = []
        self._installed = False
        self._originals: dict[str, Callable[..., Any]] = {}

    def _allow(self, target: object) -> bool:
        return self.allow_loopback and _is_loopback_host(_host_from_address(target))

    def _block(self, operation: str, target: object) -> None:
        target_string = NetworkViolation._safe_target(target)
        self.attempts.append(NetworkAttempt(operation=operation, target=target_string))
        raise NetworkViolation(operation, target)

    def __enter__(self) -> "NetworkGuard":
        if self._installed:
            return self
        self._installed = True
        self._originals = {
            "create_connection": socket.create_connection,
            "getaddrinfo": socket.getaddrinfo,
            "connect": socket.socket.connect,
            "connect_ex": socket.socket.connect_ex,
            "sendto": socket.socket.sendto,
        }

        def create_connection(address: object, *args: Any, **kwargs: Any) -> socket.socket:
            if not self._allow(address):
                self._block("socket.create_connection", address)
            return self._originals["create_connection"](address, *args, **kwargs)

        def getaddrinfo(host: object, *args: Any, **kwargs: Any) -> Any:
            if not self._allow(host):
                self._block("socket.getaddrinfo", host)
            return self._originals["getaddrinfo"](host, *args, **kwargs)

        def connect(sock: socket.socket, address: object) -> Any:
            if not self._allow(address):
                self._block("socket.connect", address)
            return self._originals["connect"](sock, address)

        def connect_ex(sock: socket.socket, address: object) -> int:
            if not self._allow(address):
                self._block("socket.connect_ex", address)
            return self._originals["connect_ex"](sock, address)

        def sendto(sock: socket.socket, data: bytes, address: object, *args: Any) -> int:
            if not self._allow(address):
                self._block("socket.sendto", address)
            return self._originals["sendto"](sock, data, address, *args)

        socket.create_connection = create_connection
        socket.getaddrinfo = getaddrinfo
        socket.socket.connect = connect
        socket.socket.connect_ex = connect_ex
        socket.socket.sendto = sendto
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if not self._installed:
            return
        socket.create_connection = self._originals["create_connection"]
        socket.getaddrinfo = self._originals["getaddrinfo"]
        socket.socket.connect = self._originals["connect"]
        socket.socket.connect_ex = self._originals["connect_ex"]
        socket.socket.sendto = self._originals["sendto"]
        self._installed = False


_global_lock = threading.Lock()
_global_guard: NetworkGuard | None = None


def install_global_network_guard() -> NetworkGuard:
    """Install a process-lifetime guard for a spawned plugin test process."""

    global _global_guard
    with _global_lock:
        if _global_guard is None:
            _global_guard = NetworkGuard(allow_loopback=True)
            _global_guard.__enter__()
        return _global_guard
