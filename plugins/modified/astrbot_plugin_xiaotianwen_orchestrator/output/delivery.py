"""One final delivery owner and cancellation-safe delivery decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..contracts.validation import require_identifier, require_non_empty_string
from .idempotency import DeliveryIdempotencyStore


class DeliveryStatus(str, Enum):
    ALLOWED = "ALLOWED"
    DUPLICATE = "DUPLICATE"
    CANCELLED = "CANCELLED"
    WRONG_OWNER = "WRONG_OWNER"
    AUDIT_REQUIRED = "AUDIT_REQUIRED"


@dataclass(frozen=True, slots=True)
class DeliveryDecision:
    request_id: str
    delivery_id: str
    owner: str
    status: DeliveryStatus
    reason: str

    @property
    def allowed(self) -> bool:
        return self.status is DeliveryStatus.ALLOWED


class DeliveryCoordinator:
    """Tracks ownership, audit passage and late/duplicate suppression."""

    def __init__(self, *, max_requests: int = 1_000) -> None:
        self.idempotency = DeliveryIdempotencyStore(max_requests=max_requests)
        self._owner_by_request: dict[str, str] = {}
        self._cancelled: set[str] = set()
        self._audit_passed: set[str] = set()
        self._send_count: dict[str, int] = {}

    def claim_owner(self, request_id: str, owner: str) -> bool:
        request = require_identifier(request_id, "request_id")
        normalized_owner = require_non_empty_string(owner, "owner")
        previous = self._owner_by_request.get(request)
        if previous is None:
            self._owner_by_request[request] = normalized_owner
            return True
        return previous == normalized_owner

    def mark_audit_passed(self, request_id: str) -> None:
        self._audit_passed.add(require_identifier(request_id, "request_id"))

    def cancel(self, request_id: str) -> None:
        self._cancelled.add(require_identifier(request_id, "request_id"))

    def attempt(
        self,
        request_id: str,
        *,
        owner: str,
        delivery_id: str,
        requires_audit: bool = True,
    ) -> DeliveryDecision:
        request = require_identifier(request_id, "request_id")
        normalized_owner = require_non_empty_string(owner, "owner")
        delivery = require_identifier(delivery_id, "delivery_id")
        registered_owner = self._owner_by_request.get(request)
        if registered_owner != normalized_owner:
            return DeliveryDecision(
                request,
                delivery,
                normalized_owner,
                DeliveryStatus.WRONG_OWNER,
                "request has another final delivery owner",
            )
        if request in self._cancelled:
            return DeliveryDecision(
                request,
                delivery,
                normalized_owner,
                DeliveryStatus.CANCELLED,
                "request was cancelled; late delivery suppressed",
            )
        if requires_audit and request not in self._audit_passed:
            return DeliveryDecision(
                request,
                delivery,
                normalized_owner,
                DeliveryStatus.AUDIT_REQUIRED,
                "output audit has not passed",
            )
        if not self.idempotency.claim(request, delivery):
            return DeliveryDecision(
                request,
                delivery,
                normalized_owner,
                DeliveryStatus.DUPLICATE,
                "delivery idempotency key already claimed",
            )
        self._send_count[request] = self._send_count.get(request, 0) + 1
        return DeliveryDecision(
            request,
            delivery,
            normalized_owner,
            DeliveryStatus.ALLOWED,
            "delivery claim accepted",
        )

    def send_count(self, request_id: str) -> int:
        return self._send_count.get(request_id, 0)

    def structural_summary(self, request_id: str) -> dict[str, object]:
        return {
            "request_id": request_id,
            "owner": self._owner_by_request.get(request_id),
            "cancelled": request_id in self._cancelled,
            "audit_passed": request_id in self._audit_passed,
            "send_count": self.send_count(request_id),
        }
