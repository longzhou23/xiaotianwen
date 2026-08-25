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


def test_standalone_political_word_is_conservatively_blocked():
    finding = LocalRules().inspect("政治")
    assert finding is not None
    assert finding.category == "POLITICAL_SENSITIVE"
