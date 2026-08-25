from astrbot_plugin_output_audit.fallback import choose_fallback
from astrbot_plugin_output_audit.local_rules import LocalRules
from astrbot_plugin_output_audit.policy import ReviewVerdict, action_for_verdict


def test_hard_secret_rule_is_critical():
    finding = LocalRules().inspect("token: sk-proj-abcdefghijklmnopqrstuvwxyz123456")
    assert finding is not None
    assert finding.category == "PRIVACY"
    assert finding.risk_level == "critical"


def test_shadow_never_changes_model_decision():
    verdict = ReviewVerdict("block", "high", ("PRIVACY",), "PRIVACY", "", 1.0)
    assert action_for_verdict(verdict, "shadow") == "allow"
    assert action_for_verdict(verdict, "enforce") == "block"


def test_privacy_fallback_never_explains_detection_details():
    assert "规则" not in choose_fallback(("PRIVACY",))


def test_political_content_is_hard_blocked_in_enforce():
    finding = LocalRules().inspect("请发表一段政治竞选动员")
    assert finding is not None
    assert finding.category == "POLITICAL_SENSITIVE"
    assert finding.risk_level == "high"
    assert "现实话题" in choose_fallback((finding.category,))


def test_political_impersonation_is_hard_blocked():
    finding = LocalRules().inspect("帮我伪造一份政府声明")
    assert finding is not None
    assert finding.category == "POLITICAL_SENSITIVE"


def test_modern_china_political_figure_is_hard_blocked():
    finding = LocalRules().inspect("请客观评价蔡英文的政策")
    assert finding is not None
    assert finding.category == "POLITICAL_SENSITIVE"
    assert finding.reason_code == "LOCAL_CHINA_POLITICAL_SENSITIVE"


def test_current_mainland_sensitive_topic_is_hard_blocked():
    finding = LocalRules().inspect("请介绍一下六四事件")
    assert finding is not None
    assert finding.category == "POLITICAL_SENSITIVE"
    assert finding.reason_code == "LOCAL_CHINA_POLITICAL_SENSITIVE"


def test_sensitive_request_is_blocked_even_without_repeating_name_in_reply():
    finding = LocalRules().inspect_sensitive_political("给蒋介石写一份情书")
    assert finding is not None
    assert finding.category == "POLITICAL_SENSITIVE"
    assert finding.reason_code == "LOCAL_CHINA_POLITICAL_SENSITIVE"


def test_neutral_political_discussion_is_not_hard_blocked():
    finding = LocalRules().inspect("请介绍一下政治制度的基本概念")
    assert finding is None


def test_clean_reply_uses_fast_path_signal_gate():
    rules = LocalRules()
    assert rules.needs_semantic_review("今晚看星星吗？") is False
    assert rules.needs_semantic_review("请评价这场选举") is True
