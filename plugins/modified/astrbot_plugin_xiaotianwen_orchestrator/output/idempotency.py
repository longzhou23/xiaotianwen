"""Bounded request-local delivery idempotency keys."""

from __future__ import annotations

from ..contracts.validation import ContractValidationError, require_identifier


class DeliveryIdempotencyStore:
    def __init__(self, *, max_requests: int = 1_000) -> None:
        if type(max_requests) is not int or max_requests <= 0:
            raise ContractValidationError("max_requests must be positive")
        self.max_requests = max_requests
        self._keys: dict[str, set[str]] = {}

    def claim(self, request_id: str, key: str) -> bool:
        request = require_identifier(request_id, "request_id")
        idempotency_key = require_identifier(key, "idempotency_key")
        if request not in self._keys:
            self._keys[request] = set()
        keys = self._keys[request]
        if idempotency_key in keys:
            return False
        keys.add(idempotency_key)
        while len(self._keys) > self.max_requests:
            self._keys.pop(next(iter(self._keys)))
        return True

    def contains(self, request_id: str, key: str) -> bool:
        return key in self._keys.get(request_id, ())

    def discard_request(self, request_id: str) -> None:
        self._keys.pop(request_id, None)

    @property
    def request_count(self) -> int:
        return len(self._keys)
