"""P2r.1a-E1 bounded evaluator runtime tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from iris_memory.cognitive.contracts import ResolvedEvent
from iris_memory.cognitive.inbound_semantic_authority import (
    InboundSemanticActAuthorityServiceV1,
    InboundSemanticActAuthorityStoreV1,
    InboundSemanticAuthorityIntegrityError,
    InboundSemanticDecision,
    create_runtime_semantic_authority_service,
)
from iris_memory.cognitive.reply_link_authority import (
    InboundReplyReferenceFactV1,
    PlatformMessageIdentityV1,
)

try:
    from main import IrisMemoryPlugin
except ImportError:  # pragma: no cover - package-only test discovery
    IrisMemoryPlugin = None  # type: ignore[assignment,misc]
from iris_memory.cognitive.semantic_evaluator_runtime import (
    CANONICAL_SYSTEM_PROMPT,
    EXPECTED_PROMPT_TEMPLATE_HASH,
    EXPECTED_RUNTIME_PROFILE_HASH,
    RUNTIME_MODEL,
    RUNTIME_PROVIDER_ID,
    AstrBotProviderSemanticAdapterV1,
    ExplicitCorrectionEvaluatorProfileV1,
    SemanticEvaluatorExecutionPolicyV1,
    SemanticEvaluatorOutputError,
    build_evaluator_input,
    build_evaluator_prompt,
    create_runtime_semantic_evaluator,
    create_test_semantic_evaluator_worker,
    parse_closed_decision_output,
)


class _FakeProvider:
    def __init__(self, output: str = '{"decision":"MATCH"}') -> None:
        self.output = output
        self.calls: list[dict[str, object]] = []
        self.active = 0
        self.max_active = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.raise_error = False

    def meta(self):
        return SimpleNamespace(
            id="provider.test",
            type="fake_chat_completion",
            provider_type="chat_completion",
            model="model.test",
        )

    async def text_chat(self, **kwargs):
        self.calls.append(kwargs)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            if self.raise_error:
                raise RuntimeError("secret prompt should not escape")
            if self.release.is_set():
                await asyncio.sleep(0)
            else:
                await asyncio.wait_for(self.release.wait(), timeout=0.2)
            return SimpleNamespace(completion_text=self.output)
        finally:
            self.active -= 1


class _TargetProvider(_FakeProvider):
    def meta(self):
        return SimpleNamespace(
            id=RUNTIME_PROVIDER_ID,
            type="chatgpt_codex",
            provider_type="chat_completion",
            model=RUNTIME_MODEL,
        )


def _profile() -> ExplicitCorrectionEvaluatorProfileV1:
    return ExplicitCorrectionEvaluatorProfileV1(
        provider_id="provider.test",
        provider_type="fake_chat_completion",
        provider_family="chat_completion",
        model="model.test",
    )


def _input(profile: ExplicitCorrectionEvaluatorProfileV1, content: str = "不是，我说的是……"):
    return build_evaluator_input(
        profile=profile,
        source_event_id="event-1",
        source_platform_message_identity=PlatformMessageIdentityV1(
            "napcat", "bot", "group", "event-1"
        ),
        inbound_reply_fact_id="fact-1",
        content=content,
    )


def test_profile_is_content_addressed_and_detached() -> None:
    rules = {"json_top_level": "object", "exact_keys": ("decision",)}
    profile = _profile()
    assert profile.profile_payload_hash.startswith("sha256:")
    assert profile.profile_payload_hash != ExplicitCorrectionEvaluatorProfileV1(
        provider_id="provider.test",
        provider_type="fake_chat_completion",
        provider_family="chat_completion",
        model="model.other",
    ).profile_payload_hash
    with pytest.raises(ValueError):
        ExplicitCorrectionEvaluatorProfileV1(
            provider_id="provider.test",
            provider_type="fake_chat_completion",
            provider_family="chat_completion",
            model="model.test",
            canonical_parser_rules=rules,
        )


def test_canonical_prompt_hash_and_rendering() -> None:
    assert EXPECTED_PROMPT_TEMPLATE_HASH == (
        "sha256:7f227e6b10d14d833a30a5afe386af339677a1294dddcaaf54896f551aa61d08"
    )
    rendered = build_evaluator_prompt('x"\n')
    assert rendered.startswith("Classify this exact inbound utterance:\n")
    assert '"content":"x\\\"\\n"' in rendered
    assert CANONICAL_SYSTEM_PROMPT.startswith("You are an observational speech-act classifier.")


@pytest.mark.parametrize(
    ("raw", "decision"),
    [
        ('{"decision":"MATCH"}', InboundSemanticDecision.MATCH),
        ('{"decision":"NO_MATCH"}', InboundSemanticDecision.NO_MATCH),
        ('{"decision":"ABSTAIN"}', InboundSemanticDecision.ABSTAIN),
    ],
)
def test_closed_parser_accepts_only_three_decisions(raw: str, decision: InboundSemanticDecision) -> None:
    assert parse_closed_decision_output(raw).decision is decision


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not json",
        '{"decision":"MATCH","extra":1}',
        '{"decision":"match"}',
        '{"decision":1}',
        '{"decision":"MАTCH"}',  # Cyrillic A
        '```json\n{"decision":"MATCH"}\n```',
        '{"decision":"MATCH"}{"decision":"NO_MATCH"}',
        '{"decision":"MATCH"} trailing',
    ],
)
def test_closed_parser_rejects_malformed_or_ambiguous_output(raw: str) -> None:
    with pytest.raises(SemanticEvaluatorOutputError):
        parse_closed_decision_output(raw)


@pytest.mark.asyncio
async def test_provider_adapter_sends_only_minimal_input(tmp_path: Path) -> None:
    profile = _profile()
    provider = _FakeProvider()
    adapter = AstrBotProviderSemanticAdapterV1(provider, profile)
    provider.release.set()
    result = await adapter.evaluate(_input(profile))
    assert result.decision is InboundSemanticDecision.MATCH
    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert call["contexts"] == []
    assert call["image_urls"] is None
    assert "Episode" not in str(call)
    assert "Iris" not in str(call)
    assert "Persona" not in str(call)
    assert "Host" not in str(call["prompt"])


@pytest.mark.asyncio
async def test_worker_success_persists_authority_and_reuses_singleflight(tmp_path: Path) -> None:
    profile = _profile()
    provider = _FakeProvider()
    provider.release.set()
    worker = create_test_semantic_evaluator_worker(
        str(tmp_path / "authority.jsonl"), profile=profile, provider=provider
    )
    await worker.start()
    first = worker.schedule(_input(profile))
    second = worker.schedule(_input(profile))
    assert first is not None and second is first
    authority = await first
    assert authority is not None
    assert authority.decision is InboundSemanticDecision.MATCH
    assert len(provider.calls) == 1
    assert worker.metrics["singleflight_reused"] == 1
    await worker.shutdown()


@pytest.mark.asyncio
async def test_existing_authority_skips_provider_call(tmp_path: Path) -> None:
    profile = _profile()
    provider = _FakeProvider()
    provider.release.set()
    path = str(tmp_path / "authority.jsonl")
    worker = create_test_semantic_evaluator_worker(path, profile=profile, provider=provider)
    await worker.start()
    first = worker.schedule(_input(profile))
    assert first is not None
    assert await first is not None
    await worker.shutdown()

    provider2 = _FakeProvider('{"decision":"NO_MATCH"}')
    provider2.release.set()
    reopened = create_test_semantic_evaluator_worker(path, profile=profile, provider=provider2)
    await reopened.start()
    reused = reopened.schedule(_input(profile))
    assert reused is not None
    assert await reused is not None
    assert provider2.calls == []
    await reopened.shutdown()


@pytest.mark.asyncio
async def test_timeout_provider_failure_and_store_failure_are_no_artifact(tmp_path: Path) -> None:
    profile = _profile()
    provider = _FakeProvider()
    worker = create_test_semantic_evaluator_worker(
        str(tmp_path / "timeout.jsonl"),
        profile=profile,
        provider=provider,
        policy=SemanticEvaluatorExecutionPolicyV1(timeout_seconds=0.01),
    )
    await worker.start()
    timed = worker.schedule(_input(profile))
    assert timed is not None
    assert await timed is None
    assert worker.metrics["timeout"] == 1
    assert worker.authority_service.store.authorities == ()
    await worker.shutdown()

    provider2 = _FakeProvider()
    provider2.raise_error = True
    worker2 = create_test_semantic_evaluator_worker(
        str(tmp_path / "failed.jsonl"), profile=profile, provider=provider2
    )
    await worker2.start()
    failed = worker2.schedule(_input(profile))
    assert failed is not None
    assert await failed is None
    assert worker2.metrics["failed"] == 1
    await worker2.shutdown()

    provider3 = _FakeProvider()
    provider3.release.set()
    worker3 = create_test_semantic_evaluator_worker(
        str(tmp_path / "store-failed.jsonl"), profile=profile, provider=provider3
    )

    def fail(*_args, **_kwargs):
        raise RuntimeError("store failure")

    original = worker3.authority_service.store.record_authority
    worker3.authority_service.store.record_authority = fail  # type: ignore[method-assign]
    await worker3.start()
    stored = worker3.schedule(_input(profile))
    assert stored is not None
    assert await stored is None
    assert worker3.metrics["failed"] == 1
    worker3.authority_service.store.record_authority = original  # type: ignore[method-assign]
    await worker3.shutdown()


def test_caller_cannot_persist_decision_without_evaluator(tmp_path: Path) -> None:
    profile = _profile()
    store = InboundSemanticActAuthorityStoreV1(tmp_path / "direct.jsonl")
    service = InboundSemanticActAuthorityServiceV1(
        store,
        profile=profile.to_authority_profile(),
        evaluator=None,
    )
    with pytest.raises(InboundSemanticAuthorityIntegrityError):
        service.record_detached_evaluation(_input(profile), InboundSemanticDecision.MATCH)
    assert store.authorities == ()


@pytest.mark.asyncio
async def test_raw_content_is_not_persisted_or_exposed_in_diagnostics(tmp_path: Path) -> None:
    content = "绝密 inbound marker 9f2c"
    profile = _profile()
    provider = _FakeProvider()
    provider.release.set()
    path = tmp_path / "raw-content.jsonl"
    worker = create_test_semantic_evaluator_worker(str(path), profile=profile, provider=provider)
    await worker.start()
    future = worker.schedule(_input(profile, content))
    assert future is not None
    assert await future is not None
    await worker.shutdown()

    persisted = path.read_text(encoding="utf-8")
    assert content not in persisted
    assert content not in repr(worker.metrics)
    assert content not in repr(worker.authority_service.store.authorities)


@pytest.mark.asyncio
async def test_queue_full_is_non_blocking_and_drops_without_artifact(tmp_path: Path) -> None:
    profile = _profile()
    provider = _FakeProvider()
    worker = create_test_semantic_evaluator_worker(
        str(tmp_path / "queue.jsonl"),
        profile=profile,
        provider=provider,
        policy=SemanticEvaluatorExecutionPolicyV1(
            timeout_seconds=0.2, max_concurrency=1, queue_capacity=1
        ),
    )
    await worker.start()
    first = worker.schedule(_input(profile, "不是1"))
    await provider.started.wait()
    second = worker.schedule(_input(profile, "不是2"))
    third = worker.schedule(_input(profile, "不是3"))
    assert first is not None and second is not None and third is None
    assert worker.metrics["queue_dropped"] == 1
    provider.release.set()
    assert await first is not None
    assert await second is not None
    await worker.shutdown()


@pytest.mark.asyncio
async def test_max_concurrency_is_two(tmp_path: Path) -> None:
    profile = _profile()
    provider = _FakeProvider()
    worker = create_test_semantic_evaluator_worker(
        str(tmp_path / "concurrency.jsonl"), profile=profile, provider=provider
    )
    await worker.start()
    futures = [
        worker.schedule(_input(profile, f"不是{i}")) for i in range(4)
    ]
    assert all(future is not None for future in futures)
    await asyncio.sleep(0.02)
    assert provider.max_active == 2
    provider.release.set()
    results = await asyncio.gather(*(future for future in futures if future is not None))
    assert all(result is not None for result in results)
    await worker.shutdown()


@pytest.mark.asyncio
async def test_shutdown_discards_queued_work(tmp_path: Path) -> None:
    profile = _profile()
    provider = _FakeProvider()
    worker = create_test_semantic_evaluator_worker(
        str(tmp_path / "shutdown.jsonl"),
        profile=profile,
        provider=provider,
        policy=SemanticEvaluatorExecutionPolicyV1(
            timeout_seconds=0.02, max_concurrency=1, queue_capacity=2
        ),
    )
    await worker.start()
    first = worker.schedule(_input(profile, "不是1"))
    await provider.started.wait()
    second = worker.schedule(_input(profile, "不是2"))
    assert first is not None and second is not None
    await worker.shutdown()
    assert await first is None
    assert await second is None
    assert worker.metrics["completed"] == 0


def test_runtime_factory_is_disabled(tmp_path: Path) -> None:
    service = create_runtime_semantic_authority_service(tmp_path)
    assert service.profile is None
    assert service.evaluator is None
    assert create_runtime_semantic_evaluator(tmp_path) is None


@pytest.mark.asyncio
async def test_explicit_runtime_factory_binds_exact_target(tmp_path: Path) -> None:
    provider = _TargetProvider()
    provider.release.set()
    worker = create_runtime_semantic_evaluator(
        tmp_path,
        provider=provider,
        provider_id=RUNTIME_PROVIDER_ID,
        enabled=True,
    )
    assert worker is not None
    assert worker.profile.profile_payload_hash == EXPECTED_RUNTIME_PROFILE_HASH
    assert worker.profile.provider_id == RUNTIME_PROVIDER_ID
    await worker.start()
    future = worker.schedule(_input(worker.profile))
    assert future is not None
    authority = await future
    assert authority is not None
    assert authority.decision is InboundSemanticDecision.MATCH
    await worker.shutdown()


@pytest.mark.asyncio
async def test_worker_capture_entrypoint_queues_only_validated_input(tmp_path: Path) -> None:
    provider = _TargetProvider()
    provider.release.set()
    worker = create_runtime_semantic_evaluator(
        tmp_path,
        provider=provider,
        provider_id=RUNTIME_PROVIDER_ID,
        enabled=True,
    )
    assert worker is not None
    identity = PlatformMessageIdentityV1("napcat", "bot", "group", "event-1")
    event = ResolvedEvent(
        event_id="event-1",
        source="napcat",
        occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        session_id="group",
        mode="casual_group_chat",
        content="不是，我说的是……",
        actor=None,
    )
    fact = InboundReplyReferenceFactV1.create(
        source_event_id="event-1",
        source_platform_message_identity=identity,
        reply_target_platform_message_identity=PlatformMessageIdentityV1(
            "napcat", "bot", "group", "target-1"
        ),
    )
    await worker.start()
    future = worker.schedule_after_inbound_commit(
        resolved_event=event,
        inbound_fact=fact,
        source_platform_message_identity=identity,
    )
    assert future is not None
    authority = await future
    assert authority is not None
    assert authority.decision is InboundSemanticDecision.MATCH
    await worker.shutdown()


def test_explicit_runtime_factory_fails_closed_on_wrong_target(tmp_path: Path) -> None:
    provider = _FakeProvider()
    with pytest.raises(ValueError):
        create_runtime_semantic_evaluator(
            tmp_path,
            provider=provider,
            provider_id=RUNTIME_PROVIDER_ID,
            enabled=True,
        )


def test_plugin_startup_enablement_is_explicit_and_fail_closed(tmp_path: Path) -> None:
    if IrisMemoryPlugin is None:  # pragma: no cover - defensive package import
        pytest.skip("main plugin module unavailable")

    class _Config:
        def __init__(self, enabled: object, provider_id: object) -> None:
            self.enabled = enabled
            self.provider_id = provider_id

        def get(self, key: str, default: object = None) -> object:
            return {
                "semantic_evaluator.enable": self.enabled,
                "semantic_evaluator.provider_id": self.provider_id,
            }.get(key, default)

    disabled = object.__new__(IrisMemoryPlugin)
    disabled.config = _Config(True, "")
    disabled.context = SimpleNamespace(get_provider_by_id=lambda _provider_id: _TargetProvider())
    disabled.data_dir = tmp_path / "disabled"
    disabled._init_semantic_evaluator()
    assert disabled._semantic_evaluator is None
    assert disabled._production_semantic_evaluator is None

    enabled = object.__new__(IrisMemoryPlugin)
    enabled.config = _Config(True, RUNTIME_PROVIDER_ID)
    enabled.context = SimpleNamespace(get_provider_by_id=lambda _provider_id: _TargetProvider())
    enabled.data_dir = tmp_path / "enabled"
    enabled._init_semantic_evaluator()
    assert enabled._semantic_evaluator is not None
    assert enabled._production_semantic_evaluator == "EXPLICIT_CORRECTION_V1"
