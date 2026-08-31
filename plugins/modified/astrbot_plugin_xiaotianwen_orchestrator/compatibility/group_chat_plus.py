"""Compare shadow batching with legacy observations without importing legacy code."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..contracts.validation import ContractValidationError, JsonValue, require_identifier
from ..ingress.debounce import ShadowTurnSnapshot


def _message_ids(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ContractValidationError("legacy message_ids must be a list of ids")
    normalized: list[str] = []
    for item in value:
        if type(item) not in (str, int):
            raise ContractValidationError("legacy message_ids must contain strings or integers")
        normalized.append(require_identifier(str(item), "message_id"))
    return tuple(normalized)


@dataclass(frozen=True, slots=True)
class LegacyGroupChatPlusSnapshot:
    """Only the observable routing/batching facts needed for a shadow comparison."""

    session_id: str
    message_ids: tuple[str, ...]
    creates_primary_reply: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", require_identifier(self.session_id, "session_id"))
        if type(self.creates_primary_reply) is not bool:
            raise ContractValidationError("creates_primary_reply must be a boolean")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "LegacyGroupChatPlusSnapshot":
        if not isinstance(value, Mapping):
            raise ContractValidationError("legacy observation must be a mapping")
        session_id = value.get("session_id", "")
        if type(session_id) not in (str, int):
            raise ContractValidationError("legacy session_id must be a string or integer")
        creates_primary_reply = value.get("creates_primary_reply", False)
        if type(creates_primary_reply) is not bool:
            raise ContractValidationError("legacy creates_primary_reply must be a boolean")
        return cls(
            session_id=str(session_id),
            message_ids=_message_ids(value.get("message_ids")),
            creates_primary_reply=creates_primary_reply,
        )


@dataclass(frozen=True, slots=True)
class GroupChatPlusStructuralDiff:
    """Difference report containing IDs and booleans only, never user text."""

    matches: bool
    differing_fields: tuple[str, ...]
    shadow_session_id: str
    legacy_session_id: str
    shadow_message_ids: tuple[str, ...]
    legacy_message_ids: tuple[str, ...]
    shadow_creates_primary_reply: bool
    legacy_creates_primary_reply: bool

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "matches": self.matches,
            "differing_fields": list(self.differing_fields),
            "shadow_session_id": self.shadow_session_id,
            "legacy_session_id": self.legacy_session_id,
            "shadow_message_ids": list(self.shadow_message_ids),
            "legacy_message_ids": list(self.legacy_message_ids),
            "shadow_creates_primary_reply": self.shadow_creates_primary_reply,
            "legacy_creates_primary_reply": self.legacy_creates_primary_reply,
        }


def _shadow_message_ids(turn: ShadowTurnSnapshot) -> tuple[str, ...]:
    raw = turn.turn.metadata.get("message_ids")
    if isinstance(raw, list):
        return _message_ids(raw)
    raw_single = turn.turn.metadata.get("message_id")
    return _message_ids([raw_single]) if isinstance(raw_single, str) and raw_single else ()


def compare_shadow_turn(
    turn: ShadowTurnSnapshot,
    legacy: LegacyGroupChatPlusSnapshot | Mapping[str, object],
) -> GroupChatPlusStructuralDiff:
    """Compare batch shape only; the current reply path remains untouched."""

    if not isinstance(turn, ShadowTurnSnapshot):
        raise ContractValidationError("turn must be a ShadowTurnSnapshot")
    legacy_snapshot = (
        legacy if isinstance(legacy, LegacyGroupChatPlusSnapshot) else LegacyGroupChatPlusSnapshot.from_mapping(legacy)
    )
    shadow_message_ids = _shadow_message_ids(turn)
    # Shadow mode deliberately does not send. The expected legacy behavior is
    # represented by whether the observed batch produced its one main reply.
    shadow_would_create_primary_reply = turn.state.value in {
        "COLLECTING",
        "READY",
        "REQUESTING",
        "TOOL_LOOP",
        "RESPONDING",
        "COMPLETED",
    }
    fields: list[str] = []
    if turn.turn.session_id != legacy_snapshot.session_id:
        fields.append("session_id")
    if shadow_message_ids != legacy_snapshot.message_ids:
        fields.append("message_ids")
    if shadow_would_create_primary_reply != legacy_snapshot.creates_primary_reply:
        fields.append("creates_primary_reply")
    return GroupChatPlusStructuralDiff(
        matches=not fields,
        differing_fields=tuple(fields),
        shadow_session_id=turn.turn.session_id,
        legacy_session_id=legacy_snapshot.session_id,
        shadow_message_ids=shadow_message_ids,
        legacy_message_ids=legacy_snapshot.message_ids,
        shadow_creates_primary_reply=shadow_would_create_primary_reply,
        legacy_creates_primary_reply=legacy_snapshot.creates_primary_reply,
    )
