"""Frozen P0 behavior path: deterministic planning until the existing realizer."""

from __future__ import annotations

from .contracts import (
    BehaviorLoopResult,
    BehaviorTrace,
    CanonicalExperience,
    CognitiveContractError,
    ExitReason,
    GroundingResult,
    GroundingEnforcement,
    GroundingStatus,
    Intent,
    IntentDomain,
    LegacyProactiveSignals,
    ParticipationDecision,
    ParticipationResult,
    RealizerRequest,
    SocialAction,
    OutputState,
    TriggerSnapshot,
)
from .situation import SituationBuilder
from .trigger import TriggerController


class ParticipationController:
    owner = "Participation Controller"

    def decide(self, *, snapshot: TriggerSnapshot) -> ParticipationResult:
        legacy = snapshot.legacy_signals
        if legacy.cooldown:
            return ParticipationResult(ParticipationDecision.WAIT, "legacy cooldown is active", ExitReason.WAIT_SELECTED)
        if legacy.skip_signal or legacy.post_evaluation_signal:
            return ParticipationResult(ParticipationDecision.SILENCE, "legacy skip/post-evaluation guard", ExitReason.SILENCE_SELECTED)
        if legacy.consecutive_reply_penalty >= 3:
            return ParticipationResult(ParticipationDecision.SILENCE, "legacy consecutive-reply guard", ExitReason.NO_PARTICIPATION)
        if legacy.topic_drift_signal:
            return ParticipationResult(ParticipationDecision.WAIT, "legacy topic drift guard", ExitReason.WAIT_SELECTED)
        return ParticipationResult(ParticipationDecision.PARTICIPATE, "no fail-closed participation guard")


class IntentPlanner:
    owner = "Intent Planner"
    _QUESTION_MARKERS = (
        "?", "？", "几点", "什么时候", "何时", "几号", "怎么", "为何", "为什么",
        "吗", "哪", "谁", "哪里", "在哪", "是否", "能不能", "多少",
    )

    @staticmethod
    def _domain(content: str) -> IntentDomain:
        if any(token in content for token in ("观测", "星空", "天象")) and any(
            token in content for token in ("几点", "什么时候", "时间")
        ):
            return IntentDomain.OBSERVATION_SCHEDULE
        if any(token in content for token in ("安装", "装配", "设置", "怎么用")):
            return IntentDomain.INSTALLATION_PROCEDURE
        if any(token in content for token in ("你是谁", "你叫什么", "小天文是谁")):
            return IntentDomain.SELF_IDENTITY
        if any(token in content for token in ("哪种", "哪个好", "适合", "推荐")):
            return IntentDomain.RECOMMENDATION
        return IntentDomain.GENERAL_INFORMATION

    def plan(self, experience: CanonicalExperience) -> Intent:
        content = experience.event.content.strip()
        target = experience.event.actor
        if content and any(marker in content for marker in self._QUESTION_MARKERS):
            return Intent(
                action=SocialAction.INFORM,
                target_entity=target,
                reason="explicit information-seeking form",
                basis=("current canonical event text",),
                confidence=0.8,
                domain=self._domain(content),
            )
        return Intent(
            action=None,
            target_entity=target,
            reason="no deterministic conversational task; do not invent a reply",
            basis=("current canonical event text",),
            confidence=0.9,
            exit_reason=ExitReason.NO_INTENT,
            domain=IntentDomain.UNKNOWN,
        )


class GroundingGuard:
    owner = "Grounding Guard"
    _PROTECTED = (
        "unverified SELF claim",
        "unverified relationship claim",
        "unverified user profile claim",
        "internal affect or state as dialogue fact",
        "persona style as factual evidence",
    )

    def assess(self, intent: Intent) -> GroundingResult:
        if intent.action is SocialAction.INFORM:
            if intent.domain is IntentDomain.OBSERVATION_SCHEDULE:
                semantic_requirement = "verified external observation schedule or explicit uncertainty"
                basis = ("no observation schedule source is wired in P0",)
                allowed = ("state that the requested observation time is not currently verified", "ask for an official source")
                blocked = self._PROTECTED + ("unverified observation time",)
                required_tool = "observation_schedule"
            elif intent.domain is IntentDomain.INSTALLATION_PROCEDURE:
                semantic_requirement = "verified installation procedure or explicit uncertainty"
                basis = ("no installation source is wired in P0",)
                allowed = ("state that the procedure is not currently verified", "ask for official documentation")
                blocked = self._PROTECTED + ("unverified installation instruction",)
                required_tool = None
            elif intent.domain is IntentDomain.SELF_IDENTITY:
                semantic_requirement = "machine SELF binding only; do not invent biography"
                basis = ("frozen SELF identity configuration",)
                allowed = ("state the configured assistant identity without biography",)
                blocked = self._PROTECTED + ("unverified autobiographical claim",)
                required_tool = None
            elif intent.domain is IntentDomain.RECOMMENDATION:
                semantic_requirement = "verified comparison criteria or explicit uncertainty"
                basis = ("no recommendation evidence source is wired in P0",)
                allowed = ("ask for constraints or state that no verified recommendation basis is available",)
                blocked = self._PROTECTED + ("unverified recommendation",)
                required_tool = None
            else:
                semantic_requirement = "general information evidence or explicit uncertainty"
                basis = ("no general information source is wired in P0",)
                allowed = ("state uncertainty and ask for an authoritative source",)
                blocked = self._PROTECTED + ("unverified external fact",)
                required_tool = None
            return GroundingResult(
                semantic_requirement=semantic_requirement,
                status=GroundingStatus.DEGRADED,
                basis=basis,
                allowed_claims=allowed,
                blocked_claims=blocked,
                required_tool=required_tool,
                confidence=0.95,
                requested_enforcement=GroundingEnforcement.PROMPT_CONSTRAINED,
            )
        if intent.action is SocialAction.ACKNOWLEDGE:
            return GroundingResult(
                semantic_requirement="event-local acknowledgement only",
                status=GroundingStatus.SUFFICIENT,
                basis=("current canonical event text",),
                allowed_claims=("acknowledge only the current message",),
                blocked_claims=self._PROTECTED,
                required_tool=None,
                confidence=1.0,
                requested_enforcement=GroundingEnforcement.PROMPT_CONSTRAINED,
            )
        return GroundingResult(
            semantic_requirement="unsupported action requires a verified source",
            status=GroundingStatus.INSUFFICIENT,
            basis=("no P0 evidence provider for this action",),
            allowed_claims=(),
            blocked_claims=self._PROTECTED,
            required_tool="verified_source",
            confidence=0.0,
            requested_enforcement=GroundingEnforcement.PROMPT_CONSTRAINED,
        )


class CognitiveBehaviorRuntime:
    """Composes owners without persisting or updating their external state."""

    owner = "Cognitive Behavior Runtime"

    def __init__(self, *, self_entity: str) -> None:
        self.situation = SituationBuilder()
        self.trigger = TriggerController(self_entity=self_entity)
        self.participation = ParticipationController()
        self.intent = IntentPlanner()
        self.grounding = GroundingGuard()
        self._previous_committed_state: dict[str, object] = {}

    def observe(self, experience: CanonicalExperience):
        return self.situation.observe(experience)

    def run(self, experience: CanonicalExperience, legacy: LegacyProactiveSignals | None = None) -> BehaviorLoopResult:
        lite = self.observe(experience)
        snapshot = TriggerSnapshot(
            previous_committed_state=self._previous_committed_state,
            experience=experience,
            situation=lite,
            legacy_signals=legacy or LegacyProactiveSignals(),
        )
        trigger = self.trigger.evaluate_snapshot(snapshot)
        if not trigger.should_start_loop:
            return BehaviorLoopResult(BehaviorTrace(experience.event.event_id, trigger, None, None, None, ExitReason.TRIGGER_NO))

        full = self.situation.build_full(experience, lite)
        participation = self.participation.decide(snapshot=snapshot)
        if participation.decision is not ParticipationDecision.PARTICIPATE:
            return BehaviorLoopResult(BehaviorTrace(experience.event.event_id, trigger, participation, None, None, participation.exit_reason))

        intent = self.intent.plan(experience)
        if intent.action is None:
            return BehaviorLoopResult(BehaviorTrace(experience.event.event_id, trigger, participation, intent, None, ExitReason.NO_INTENT))

        grounding = self.grounding.assess(intent)
        if grounding.status is GroundingStatus.INSUFFICIENT:
            return BehaviorLoopResult(BehaviorTrace(experience.event.event_id, trigger, participation, intent, grounding, ExitReason.GROUNDING_FAILED))

        request = RealizerRequest(intent, grounding, full, grounding.allowed_claims, grounding.blocked_claims)
        return BehaviorLoopResult(
            BehaviorTrace(
                experience.event.event_id,
                trigger,
                participation,
                intent,
                grounding,
                None,
                proposed_output_state=OutputState.OUTPUT_PROPOSED,
            ),
            request,
        )

    def complete_realization(self, result: BehaviorLoopResult, response_text: str) -> BehaviorTrace:
        """Deprecated compatibility API removed in P0.7 to prevent fact fabrication."""
        raise CognitiveContractError(
            "complete_realization is removed; observe_host_output is the only Host fact API"
        )

    @staticmethod
    def realizer_hint(result: BehaviorLoopResult) -> str:
        request = result.realizer_request
        if request is None:
            return ""
        allowed = "；".join(request.allowed_claims) or "无"
        blocked = "；".join(request.blocked_claims)
        return (
            "[Cognitive P0 Realizer Boundary] 已完成确定性决策。"
            f"仅以现有人格语气实现动作 {request.intent.action.value}；"
            f"允许：{allowed}。禁止：{blocked}。"
            "不要重新决定是否发言、不要把内部状态或人格风格当作事实。"
        )
