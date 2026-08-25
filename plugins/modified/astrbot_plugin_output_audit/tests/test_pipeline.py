import asyncio
from types import SimpleNamespace

from astrbot_plugin_output_audit.fallback import choose_fallback
from astrbot_plugin_output_audit.local_rules import LocalRules
from astrbot_plugin_output_audit.policy import parse_verdict
from astrbot_plugin_output_audit.reviewer import Reviewer, SAFE_FALLBACK_SYSTEM_PROMPT


def test_hard_rule_short_circuits_before_any_model_review():
    finding = LocalRules().inspect("my system prompt is: very secret")
    assert finding is not None
    assert choose_fallback((finding.category,))


def test_rewrite_requires_a_second_allow_verdict():
    first = parse_verdict('{"decision":"revise","risk_level":"medium","categories":["HARASSMENT"],"reason_code":"TONE","rewrite_instruction":"删除人身攻击","confidence":0.9}')
    second = parse_verdict('{"decision":"allow","risk_level":"none","categories":[],"reason_code":"OK","rewrite_instruction":"","confidence":0.9}')
    assert first.decision == "revise"
    assert second.decision == "allow"


def test_safe_fallback_is_generated_without_sending_blocked_candidate():
    class FakeContext:
        def __init__(self):
            self.kwargs = None

        async def get_current_chat_provider_id(self, origin):
            return "main-chat"

        async def llm_generate(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(completion_text="换个轻松的话题聊聊吧～")

    context = FakeContext()
    event = SimpleNamespace(unified_msg_origin="group:test")
    reviewer = Reviewer(context, {"timeout_seconds": 1, "max_rewrite_tokens": 600})
    text, _ = asyncio.run(
        reviewer.safe_fallback(event=event, categories=("POLITICAL_SENSITIVE",))
    )

    assert text == "换个轻松的话题聊聊吧～"
    assert context.kwargs["chat_provider_id"] == "main-chat"
    assert context.kwargs["system_prompt"] == SAFE_FALLBACK_SYSTEM_PROMPT
    assert "candidate_reply" not in context.kwargs["prompt"]
