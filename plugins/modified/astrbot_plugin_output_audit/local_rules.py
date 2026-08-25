"""Fast, local-only checks for information that must never leave the bot."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class LocalRuleFinding:
    category: str
    risk_level: str
    reason_code: str


class LocalRules:
    """Conservative hard rules; semantic moderation remains the review model's job."""

    _secret_patterns = (
        re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
        re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
        re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
        re.compile(r"\b(?:xox[baprs]-)[A-Za-z0-9-]{20,}\b", re.I),
        re.compile(
            r"(?i)\b(?:api[_ -]?key|access[_ -]?token|secret[_ -]?key|password)\b\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{16,}"
        ),
    )
    _prompt_patterns = (
        re.compile(r"(?i)(?:my|the|我的|系统的)\s*(?:system\s*)?(?:prompt|提示词).{0,24}(?:is|为|如下|：|:)"),
        re.compile(r"(?i)(?:developer message|开发者消息|隐藏上下文|hidden context).{0,24}(?:如下|：|:)"),
    )
    _tool_patterns = (
        re.compile(r"(?i)(?:tool[_ -]?schema|工具\s*(?:schema|参数|内部说明)).{0,24}(?:如下|：|:)"),
    )

    # Political gate is behavior-based. Topic words such as “政治”, “政府”,
    # or “战争” are not enough to block a neutral explanation or evaluation;
    # direct persuasion, mobilization, impersonation, and incitement are.
    _political_patterns = (
        re.compile(
            r"(?i)\b(?:vote for|support (?:the )?(?:party|candidate)|political campaign|political mobilization|incite (?:violence|hatred|crime)|overthrow the government|fake government statement|impersonate (?:the )?government)\b"
        ),
        re.compile(
            r"(?:投票给|请支持.{0,12}(?:党|候选人)|支持.{0,12}(?:党|候选人)|为.{0,12}拉票|竞选动员|政治动员|煽动(?:暴力|仇恨|违法)|推翻政府|武装起义|制造政治事件|伪造.{0,8}(?:政府|官方)(?:机构)?声明|冒充(?:政府|官方机构)|规避政治审查)"
        ),
    )
    # Explicit user-requested blacklist for modern Chinese politics. Unlike
    # general political discussion, these names and current mainland-sensitive
    # topics are blocked even when phrased as an evaluation or introduction.
    _china_sensitive_political_patterns = (
        re.compile(
            r"(?:毛泽东|周恩来|刘少奇|邓小平|江泽民|胡锦涛|习近平|李克强|李强|胡耀邦|赵紫阳|华国锋|温家宝|朱镕基|蒋介石|蒋经国|李登辉|陈水扁|马英九|蔡英文|赖清德|柯文哲|侯友宜|朱立伦|韩国瑜|郭台铭)"
        ),
        re.compile(
            r"(?:六四|八九民运|天安门事件|天安门广场|坦克人|法轮功|白纸运动|四通桥|再教育营|新疆人权|维吾尔人权|西藏独立|藏独|台湾独立|台独|反送中|雨伞运动|香港民主运动|香港抗议|香港国安法)"
        ),
    )
    _semantic_signal_patterns = (
        re.compile(
            r"(?i)(?:system prompt|developer message|hidden context|api[_ -]?key|access[_ -]?token|password|private memory|reveal secret|suicide|self-harm|kill myself|weapon|explosive|political|election|government|president|war|territory)"
        ),
        re.compile(
            r"(?:系统提示词|开发者消息|隐藏上下文|私密记忆|泄露秘密|自杀|自残|杀人|武器|爆炸物|色情|裸聊|未成年人|违法|政治|选举|政府|总统|战争|领土|主权|抗议|示威)"
        ),
    )

    def inspect(self, text: str) -> LocalRuleFinding | None:
        sample = text or ""
        if any(pattern.search(sample) for pattern in self._secret_patterns):
            return LocalRuleFinding("PRIVACY", "critical", "LOCAL_SECRET_PATTERN")
        if any(pattern.search(sample) for pattern in self._prompt_patterns):
            return LocalRuleFinding("PROMPT_LEAK", "high", "LOCAL_PROMPT_DISCLOSURE")
        if any(pattern.search(sample) for pattern in self._tool_patterns):
            return LocalRuleFinding("TOOL_DISCLOSURE", "high", "LOCAL_TOOL_DISCLOSURE")
        finding = self.inspect_sensitive_political(sample)
        if finding is not None:
            return finding
        if any(pattern.search(sample) for pattern in self._political_patterns):
            return LocalRuleFinding("POLITICAL_SENSITIVE", "high", "LOCAL_POLITICAL_CONTENT")
        return None

    def inspect_sensitive_political(self, text: str) -> LocalRuleFinding | None:
        """Check the strict China-politics blacklist without other rules.

        This is also applied to the triggering user message: a model may omit
        the sensitive person's name from its final prose, but the request must
        still not be fulfilled.
        """
        sample = text or ""
        if any(pattern.search(sample) for pattern in self._china_sensitive_political_patterns):
            return LocalRuleFinding(
                "POLITICAL_SENSITIVE", "high", "LOCAL_CHINA_POLITICAL_SENSITIVE"
            )
        return None

    def needs_semantic_review(self, text: str) -> bool:
        """Return whether a non-hard-blocked candidate merits an AI pass."""
        sample = text or ""
        return any(pattern.search(sample) for pattern in self._semantic_signal_patterns)
