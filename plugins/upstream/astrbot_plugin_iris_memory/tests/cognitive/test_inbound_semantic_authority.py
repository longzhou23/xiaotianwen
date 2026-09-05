"""Focused P2r.1a capture-time semantic authority tests."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from iris_memory.cognitive import reply_link_capture as capture_module
from iris_memory.cognitive.contracts import (
    CanonicalExperience,
    Perspective,
    ResolvedEvent,
)
from iris_memory.cognitive.inbound_semantic_authority import (
    AUTHORITY_PREFIX,
    INBOUND_SEMANTIC_AUTHORITY_SCHEMA,
    InboundSemanticActAuthorityServiceV1,
    InboundSemanticActAuthorityStoreV1,
    InboundSemanticAuthorityIntegrityError,
    InboundSemanticDecision,
    InboundSemanticEvaluatorProfileV1,
    create_runtime_semantic_authority_service,
)
from iris_memory.cognitive.iris_adapter import CognitiveRuntime
from iris_memory.cognitive.reply_link_authority import (
    InboundReplyReferenceFactV1,
    P2r0Store,
    PlatformMessageIdentityV1,
)


class _Reply:
    def __init__(self, message_id: str):
        self.id = message_id


class _Event:
    def __init__(self, resolved_event: ResolvedEvent):
        self.message_obj = SimpleNamespace(message_id="source-1")
        self.message_str = resolved_event.content
        self._resolved_event = resolved_event

    def get_extra(self, key: str):
        return None

    def set_extra(self, _key: str, _value: object) -> None:
        return None

    def get_messages(self):
        return [_Reply("target-1")]

    def get_platform_id(self):
        return "napcat-instance-1"

    def get_self_id(self):
        return "bot-1"

    def get_group_id(self):
        return "group-1"

    def get_sender_id(self):
        return "user-1"


class _AttachedPreAdapter:
    def __init__(self, resolved_event: ResolvedEvent):
        self.result = SimpleNamespace(experience=CanonicalExperience(
            id=f"experience:{resolved_event.event_id}",
            event=resolved_event,
            subject=None,
            perspective=Perspective.UNRESOLVED,
            provenance=("fixture",),
        ))
        self.calls = 0

    def attached(self, _event):
        return self.result

    def attach(self, _event):
        self.calls += 1
        return self.result


class _Evaluator:
    def __init__(self, result):
        self.result = result
        self.inputs = []

    def evaluate(self, value):
        self.inputs.append(value)
        return self.result


def _profile() -> InboundSemanticEvaluatorProfileV1:
    return InboundSemanticEvaluatorProfileV1(
        profile_id="fixture.inbound-correction",
        profile_version="1",
        evaluator_kind="TEST_FIXTURE",
        provider="fixture",
        model="fixture",
        model_version="1",
        prompt_template_hash="fixture-template-v1",
    )


def _identity() -> PlatformMessageIdentityV1:
    return PlatformMessageIdentityV1("napcat-instance-1", "bot-1", "group-1", "source-1")


def _fact() -> InboundReplyReferenceFactV1:
    return InboundReplyReferenceFactV1.create(
        source_event_id="napcat:source-1",
        source_platform_message_identity=_identity(),
        reply_target_platform_message_identity=PlatformMessageIdentityV1(
            "napcat-instance-1", "bot-1", "group-1", "target-1"
        ),
    )


def _event(content: str = "不是，我说的是……") -> ResolvedEvent:
    return ResolvedEvent(
        event_id="napcat:source-1",
        source="napcat",
        occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        session_id="group-1",
        mode="casual_group_chat",
        content=content,
        actor=None,
    )


@pytest.mark.parametrize("decision", list(InboundSemanticDecision))
def test_valid_decisions_create_one_authority(tmp_path: Path, decision):
    evaluator = _Evaluator({"decision": decision.value})
    service = InboundSemanticActAuthorityServiceV1(
        InboundSemanticActAuthorityStoreV1(tmp_path / "authority.jsonl"),
        profile=_profile(),
        evaluator=evaluator,
    )
    authority = service.evaluate_after_inbound_commit(
        resolved_event=_event(),
        inbound_fact=_fact(),
        source_platform_message_identity=_identity(),
    )
    assert authority is not None
    assert authority.decision is decision
    assert authority.authority_id.startswith(AUTHORITY_PREFIX)
    assert len(service.store.authorities) == 1
    assert "不是，我说的是" not in (tmp_path / "authority.jsonl").read_text(encoding="utf-8")


def test_capture_reuses_attached_event_and_commits_fact_before_semantics(tmp_path: Path, monkeypatch):
    resolved = _event()
    runtime = CognitiveRuntime(record_traces=False)
    adapter = _AttachedPreAdapter(resolved)
    runtime.pre_adapter = adapter
    evaluator = _Evaluator({"decision": "MATCH"})
    semantic = InboundSemanticActAuthorityServiceV1(
        InboundSemanticActAuthorityStoreV1(tmp_path / "semantic.jsonl"),
        profile=_profile(),
        evaluator=evaluator,
    )
    capture = capture_module.P2r0CaptureService(
        P2r0Store(tmp_path / "facts.jsonl"), runtime, semantic_authority_service=semantic
    )
    monkeypatch.setattr(capture_module, "_reply_type", lambda: _Reply)
    result = capture.capture_inbound(_Event(resolved))
    assert result.inbound_facts == 1
    assert len(evaluator.inputs) == 1
    assert evaluator.inputs[0].content == resolved.content
    assert adapter.calls == 0
    assert len(semantic.store.authorities) == 1


def test_no_evaluator_keeps_p2r0_capture_and_creates_no_authority(tmp_path: Path, monkeypatch):
    resolved = _event()
    runtime = CognitiveRuntime(record_traces=False)
    runtime.pre_adapter = _AttachedPreAdapter(resolved)
    semantic = create_runtime_semantic_authority_service(tmp_path)
    capture = capture_module.P2r0CaptureService(
        P2r0Store(tmp_path / "facts.jsonl"), runtime, semantic_authority_service=semantic
    )
    monkeypatch.setattr(capture_module, "_reply_type", lambda: _Reply)
    assert capture.capture_inbound(_Event(resolved)).inbound_facts == 1
    assert semantic.store.authorities == ()


def test_source_binding_mismatch_is_rejected(tmp_path: Path):
    service = InboundSemanticActAuthorityServiceV1(
        InboundSemanticActAuthorityStoreV1(tmp_path / "authority.jsonl"),
        profile=_profile(),
        evaluator=_Evaluator({"decision": "MATCH"}),
    )
    with pytest.raises(InboundSemanticAuthorityIntegrityError):
        service.evaluate_after_inbound_commit(
            resolved_event=_event(),
            inbound_fact=InboundReplyReferenceFactV1.create(
                source_event_id="napcat:other",
                source_platform_message_identity=_identity(),
                reply_target_platform_message_identity=PlatformMessageIdentityV1(
                    "napcat-instance-1", "bot-1", "group-1", "target-1"
                ),
            ),
            source_platform_message_identity=_identity(),
        )


def test_source_platform_identity_mismatch_is_rejected(tmp_path: Path):
    service = InboundSemanticActAuthorityServiceV1(
        InboundSemanticActAuthorityStoreV1(tmp_path / "authority.jsonl"),
        profile=_profile(),
        evaluator=_Evaluator({"decision": "MATCH"}),
    )
    wrong_identity = PlatformMessageIdentityV1("napcat-instance-1", "bot-1", "other-group", "source-1")
    with pytest.raises(InboundSemanticAuthorityIntegrityError):
        service.evaluate_after_inbound_commit(
            resolved_event=_event(),
            inbound_fact=_fact(),
            source_platform_message_identity=wrong_identity,
        )


def test_decision_independent_identity_conflicts_on_changed_decision(tmp_path: Path):
    store = InboundSemanticActAuthorityStoreV1(tmp_path / "authority.jsonl")
    profile = _profile()
    first = InboundSemanticActAuthorityServiceV1(
        store, profile=profile, evaluator=_Evaluator({"decision": "MATCH"})
    ).evaluate_after_inbound_commit(
        resolved_event=_event(), inbound_fact=_fact(), source_platform_message_identity=_identity()
    )
    assert first is not None
    second_service = InboundSemanticActAuthorityServiceV1(
        store, profile=profile, evaluator=_Evaluator({"decision": "NO_MATCH"})
    )
    with pytest.raises(InboundSemanticAuthorityIntegrityError):
        second_service.evaluate_after_inbound_commit(
            resolved_event=_event(), inbound_fact=_fact(), source_platform_message_identity=_identity()
        )


@pytest.mark.parametrize("content", ["ASCII", "中文", "🙂", "e\u0301", " x ", "a\nb"])
def test_exact_utf8_content_hash(tmp_path: Path, content: str):
    service = InboundSemanticActAuthorityServiceV1(
        InboundSemanticActAuthorityStoreV1(tmp_path / f"{len(content)}.jsonl"),
        profile=_profile(),
        evaluator=_Evaluator({"decision": "ABSTAIN"}),
    )
    authority = service.evaluate_after_inbound_commit(
        resolved_event=_event(content), inbound_fact=_fact(), source_platform_message_identity=_identity()
    )
    assert authority is not None
    assert authority.content_payload_hash == "sha256:" + __import__("hashlib").sha256(content.encode()).hexdigest()


def test_malformed_evaluator_output_is_fail_closed(tmp_path: Path):
    service = InboundSemanticActAuthorityServiceV1(
        InboundSemanticActAuthorityStoreV1(tmp_path / "authority.jsonl"),
        profile=_profile(),
        evaluator=_Evaluator({"decision": "MATCH", "extra": "reject"}),
    )
    with pytest.raises(InboundSemanticAuthorityIntegrityError):
        service.evaluate_after_inbound_commit(
            resolved_event=_event(), inbound_fact=_fact(), source_platform_message_identity=_identity()
        )
    assert service.store.authorities == ()


def test_semantic_append_failure_publishes_no_authority(tmp_path: Path, monkeypatch):
    store = InboundSemanticActAuthorityStoreV1(tmp_path / "authority.jsonl")
    service = InboundSemanticActAuthorityServiceV1(
        store, profile=_profile(), evaluator=_Evaluator({"decision": "MATCH"})
    )

    def fail(_record):
        raise OSError("injected append failure")

    monkeypatch.setattr(store, "_append", fail)
    with pytest.raises(InboundSemanticAuthorityIntegrityError):
        service.evaluate_after_inbound_commit(
            resolved_event=_event(), inbound_fact=_fact(), source_platform_message_identity=_identity()
        )
    assert store.authorities == ()
    assert store.get_authority("authority:p2r1a:" + "0" * 64) is None


def test_profile_is_deeply_detached_and_wrong_schema_rejected(tmp_path: Path):
    rules = {"nested": ["x"]}
    profile = InboundSemanticEvaluatorProfileV1(
        profile_id="detached", profile_version="1", evaluator_kind="TEST",
        canonical_parsing_rules=rules,
    )
    rules["nested"].append("mutated")
    assert profile.canonical_parsing_rules["nested"] == ("x",)
    with pytest.raises(InboundSemanticAuthorityIntegrityError):
        InboundSemanticEvaluatorProfileV1(
            profile_id="wrong", profile_version="1", evaluator_kind="TEST",
            schema_version=INBOUND_SEMANTIC_AUTHORITY_SCHEMA,
        )


def test_replay_is_strict_and_reconstructs(tmp_path: Path):
    store_path = tmp_path / "authority.jsonl"
    service = InboundSemanticActAuthorityServiceV1(
        InboundSemanticActAuthorityStoreV1(store_path), profile=_profile(), evaluator=_Evaluator({"decision": "MATCH"})
    )
    service.evaluate_after_inbound_commit(
        resolved_event=_event(), inbound_fact=_fact(), source_platform_message_identity=_identity()
    )
    reopened = InboundSemanticActAuthorityStoreV1(store_path)
    assert reopened.authorities[0].schema_version == INBOUND_SEMANTIC_AUTHORITY_SCHEMA
    with store_path.open("ab") as handle:
        handle.write(b"{}\n")
    with pytest.raises(InboundSemanticAuthorityIntegrityError):
        InboundSemanticActAuthorityStoreV1(store_path)
