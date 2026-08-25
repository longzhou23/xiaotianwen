"""Final-output gate for AstrBot replies.

The plugin deliberately never calls ``event.send()``.  It only replaces the
pre-send result chain, preserving AstrBot's single normal send path.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register

from .fallback import choose_fallback
from .local_rules import LocalRules
from .policy import ReviewVerdict, VerdictValidationError, action_for_verdict
from .reviewer import ReviewInput, Reviewer
from .state import RequestState
from .storage import AuditStorage


@register(
    "output_audit",
    "xtw_bot",
    "对最终自然语言回复执行本地规则与独立 AI 审核。",
    "1.0.0",
)
class OutputAuditPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._rules = LocalRules()
        self._reviewer = Reviewer(context, config)
        self._states: dict[str, RequestState] = {}
        self._state_lock = asyncio.Lock()
        self._storage: AuditStorage | None = None
        self._verdict_cache: dict[str, tuple[float, ReviewVerdict]] = {}

    async def initialize(self) -> None:
        await super().initialize()
        data_dir = Path(StarTools.get_data_dir())
        self._storage = AuditStorage(
            data_dir=data_dir,
            retention_days=int(self.config.get("audit_retention_days", 30)),
        )
        await self._storage.initialize()
        logger.info("[OutputAudit] initialized; mode=%s", self.config.get("mode", "shadow"))

    @filter.on_llm_request(priority=90)
    async def disable_streaming_for_audit(self, event: AstrMessageEvent, request: ProviderRequest) -> None:
        """Prevent incomplete text from being sent before the final gate runs."""
        if self.config.get("enabled", True) and self.config.get("disable_streaming", True):
            event.set_extra("enable_streaming", False)

    @filter.on_decorating_result(priority=-90)
    async def audit_final_result(self, event: AstrMessageEvent) -> None:
        """Audit the complete, unsegmented text immediately before core decoration."""
        if not self.config.get("enabled", True):
            return
        result = event.get_result()
        if result is None or not result.chain or not result.is_llm_result():
            return
        if event.is_stopped():
            return
        candidate = self._plain_text(result.chain)
        if not candidate:
            return
        request_id = self._request_id(event)
        candidate_hash = self._sha256(candidate)
        async with self._state_lock:
            existing = self._states.get(request_id)
            if existing and existing.finalized:
                return
            if existing and existing.candidate_hash == candidate_hash and existing.review_count:
                return
            state = RequestState(candidate_hash=candidate_hash)
            self._states[request_id] = state

        started = time.monotonic()
        decision = "unavailable"
        risk_level = "unknown"
        categories: tuple[str, ...] = ()
        reason_code = ""
        action = "allow"
        rewrote = False
        fallback_used = False
        final_text = candidate
        error_code = ""
        try:
            finding = self._rules.inspect(candidate)
            if finding is None:
                # A sensitive request can produce a harmless-looking answer
                # that omits the person's name. Apply only the explicit China
                # politics blacklist to the triggering user message so normal
                # prompt/context text does not broaden the hard gate.
                finding = self._rules.inspect_sensitive_political(event.get_message_str() or "")
            if finding is not None:
                decision, risk_level = "block", finding.risk_level
                categories, reason_code = (finding.category,), finding.reason_code
                action = "block" if self._mode() == "enforce" else "allow"
                if action == "block":
                    final_text, fallback_used = await self._safe_fallback(event, categories)
            elif not self._should_run_semantic_review(candidate):
                # Low-latency fast path: the local gate found no sensitive signal.
                decision, risk_level, reason_code, action = (
                    "allow",
                    "none",
                    "LOCAL_FAST_PATH",
                    "allow",
                )
            else:
                verdict, _ = await self._review_once(event, candidate, state)
                decision, risk_level = verdict.decision, verdict.risk_level
                categories, reason_code = verdict.categories, verdict.reason_code
                action = action_for_verdict(verdict, self._mode())
                if action == "revise":
                    final_text, fallback_used = await self._rewrite_and_recheck(event, candidate, verdict, state)
                    rewrote = True
                    action = "revise"
                elif action == "block":
                    final_text, fallback_used = await self._safe_fallback(event, categories)
        except Exception as exc:  # Never leak candidate text through logs.
            error_code = self._error_code(exc)
            logger.warning("[OutputAudit] review failed request_id=%s code=%s", request_id, error_code)
            if self._mode() == "enforce" and self.config.get("fail_policy", "rule_sensitive") == "fail_closed":
                final_text, fallback_used, action = choose_fallback(categories), True, "block"

        if final_text != candidate:
            self._replace_plain_text(result, final_text, discard_non_plain=fallback_used)
        async with self._state_lock:
            state.finalized = True
            state.final_text_hash = self._sha256(final_text)
        await self._record(
            request_id=request_id,
            event=event,
            candidate=candidate,
            final_text=final_text,
            decision=decision,
            risk_level=risk_level,
            categories=categories,
            reason_code=reason_code,
            action=action,
            rewrote=rewrote,
            fallback_used=fallback_used,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error_code=error_code,
        )

    async def _review_once(self, event: AstrMessageEvent, candidate: str, state: RequestState) -> tuple[ReviewVerdict, Any]:
        if state.review_count >= 2:
            raise RuntimeError("REVIEW_LIMIT_REACHED")
        state.review_count += 1
        review_input = ReviewInput(
            conversation_type="private" if event.is_private_chat() else "group",
            is_proactive=bool(event.get_extra("is_proactive", False)),
            input_risk_tags=tuple(event.get_extra("antipromptinjector_risk_tags", []) or [])[:4],
            recent_public_context=(),
            latest_public_message=(event.get_message_str() or "")[: int(self.config.get("max_context_chars", 1600))],
            candidate_reply=candidate,
        )
        cache_key = self._review_cache_key(event, candidate)
        cached = self._verdict_cache.get(cache_key)
        if cached and cached[0] > time.monotonic():
            return cached[1], None
        try:
            verdict, response = await self._reviewer.review(review_input)
            ttl = max(0.0, float(self.config.get("review_cache_ttl_seconds", 60)))
            if ttl:
                self._verdict_cache[cache_key] = (time.monotonic() + ttl, verdict)
            return verdict, response
        except VerdictValidationError:
            # Exactly one structured retry for malformed model output.
            if state.review_count >= 2:
                raise
            state.review_count += 1
            return await self._reviewer.review(review_input)

    def _should_run_semantic_review(self, candidate: str) -> bool:
        scope = str(self.config.get("review_scope", "risk_flagged")).lower()
        if scope == "all_final_replies":
            return True
        return self._rules.needs_semantic_review(candidate)

    def _review_cache_key(self, event: AstrMessageEvent, candidate: str) -> str:
        provider = str(self.config.get("provider_id", ""))
        conversation_type = "private" if event.is_private_chat() else "group"
        return f"output-audit-v2:{provider}:{conversation_type}:{self._sha256(candidate)}"

    async def _rewrite_and_recheck(self, event: AstrMessageEvent, candidate: str, verdict: ReviewVerdict, state: RequestState) -> tuple[str, bool]:
        if state.rewrite_count >= int(self.config.get("max_rewrite_attempts", 1)):
            return await self._safe_fallback(event, verdict.categories)
        state.rewrite_count += 1
        rewritten, _ = await self._reviewer.rewrite(event=event, candidate=candidate, verdict=verdict)
        finding = self._rules.inspect(rewritten)
        if finding is not None:
            return await self._safe_fallback(event, (finding.category,))
        checked, _ = await self._review_once(event, rewritten, state)
        if checked.decision != "allow":
            return await self._safe_fallback(event, checked.categories or verdict.categories)
        return rewritten, False

    async def _safe_fallback(self, event: AstrMessageEvent, categories: tuple[str, ...]) -> tuple[str, bool]:
        """Prefer a varied LLM refusal, retaining a deterministic last resort."""
        fallback = choose_fallback(categories)
        try:
            generated, _ = await self._reviewer.safe_fallback(event=event, categories=categories)
            generated = generated.strip()
            if not generated or len(generated) > 300:
                return fallback, True
            if self._rules.inspect(generated) is not None:
                return fallback, True
            if self._rules.inspect_sensitive_political(generated) is not None:
                return fallback, True
            return generated, True
        except Exception as exc:
            logger.warning("[OutputAudit] safe fallback generation failed code=%s", self._error_code(exc))
            return fallback, True

    def _mode(self) -> str:
        mode = str(self.config.get("mode", "shadow")).lower()
        return mode if mode in {"shadow", "warn", "enforce"} else "shadow"

    @staticmethod
    def _plain_text(chain: list[Any]) -> str:
        return "".join(comp.text for comp in chain if isinstance(comp, Comp.Plain)).strip()

    @staticmethod
    def _replace_plain_text(result: Any, text: str, *, discard_non_plain: bool) -> None:
        if discard_non_plain:
            result.chain = [Comp.Plain(text)]
            return
        new_chain: list[Any] = []
        inserted = False
        for component in result.chain:
            if isinstance(component, Comp.Plain):
                if not inserted:
                    new_chain.append(Comp.Plain(text))
                    inserted = True
                continue
            new_chain.append(component)
        if not inserted:
            new_chain.insert(0, Comp.Plain(text))
        result.chain = new_chain

    @staticmethod
    def _sha256(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _error_code(exc: Exception) -> str:
        name = type(exc).__name__.upper()
        return f"{name[:40]}"

    @staticmethod
    def _request_id(event: AstrMessageEvent) -> str:
        message_id = str(getattr(event.message_obj, "message_id", "") or "")
        return f"{event.unified_msg_origin}:{message_id or id(event)}"

    async def _record(self, **row: Any) -> None:
        if not self._storage:
            return
        try:
            await self._storage.record(
                {
                    "request_id": row["request_id"],
                    "conversation_type": "private" if row["event"].is_private_chat() else "group",
                    "provider_id": str(self.config.get("provider_id", "")),
                    "candidate_hash": self._sha256(row["candidate"]),
                    "candidate_length": len(row["candidate"]),
                    "decision": row["decision"],
                    "risk_level": row["risk_level"],
                    "categories": json.dumps(list(row["categories"])),
                    "reason_code": row["reason_code"],
                    "action": row["action"],
                    "rewrote": int(row["rewrote"]),
                    "fallback_used": int(row["fallback_used"]),
                    "elapsed_ms": row["elapsed_ms"],
                    "final_hash": self._sha256(row["final_text"]),
                    "final_length": len(row["final_text"]),
                    "error_code": row["error_code"],
                }
            )
        except Exception:
            logger.warning("[OutputAudit] audit row could not be persisted")

    async def terminate(self) -> None:
        async with self._state_lock:
            self._states.clear()
            self._verdict_cache.clear()
