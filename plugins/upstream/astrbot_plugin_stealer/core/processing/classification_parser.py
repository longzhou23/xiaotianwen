"""分类结果解析器：负责解析 VLM 的 JSON/文本响应。"""

import json
import re
from typing import Any

from astrbot.api import logger

# 打标数量上限（与 prompts.json 的 output_format 约束一致）
MAX_TAGS = 4
MAX_SCENES = 2


class ClassificationParser:
    """负责解析 VLM 的分类响应。"""

    CATEGORY_FILTERED = "过滤不通过"

    def __init__(self, plugin_instance=None) -> None:
        self.plugin = plugin_instance

    @staticmethod
    def normalize_label_list(
        values: Any,
        max_count: int,
        *,
        allow_duplicates: bool = False,
    ) -> list[str]:
        """规范化标签/场景列表：拆分字符串、去空白、去空项、保序去重、截断到上限。

        Args:
            values: VLM 输出的 tags/scenes（list 或逗号分隔字符串）
            max_count: 最多保留数量（超出截断）
            allow_duplicates: 是否允许重复（默认去重）
        """
        if isinstance(values, str):
            items = [
                s.strip()
                for s in values.replace("，", ",").replace("、", ",").replace("；", ",").split(",")
                if s.strip()
            ]
        elif isinstance(values, list):
            items = [str(v).strip() for v in values if v is not None and str(v).strip()]
        else:
            return []

        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            if not item:
                continue
            if not allow_duplicates and item in seen:
                continue
            seen.add(item)
            result.append(item)
            if len(result) >= max_count:
                break
        return result

    def _normalize_category(self, raw: str) -> str:
        """将 VLM 返回的分类文本规范化为有效分类名（委托到 ImageProcessorService）。"""
        if self.plugin and hasattr(self.plugin, "image_processor_service"):
            return self.plugin.image_processor_service._normalize_category(raw)
        return str(raw or "").strip().lower()

    def _parse_classification_response(
        self, response: str, file_path: str
    ) -> tuple[str, list[str], str, str, list[str]]:
        """Parse the classification payload returned by the VLM."""
        response = response.strip()

        data = self._extract_json_payload(response)
        if data is None:
            logger.debug(f"JSON parse failed, fallback to legacy format: {response[:100]}")
            return self._parse_legacy_format(response)

        approved = data.get("approved")
        reason = str(data.get("reason", ""))
        if (
            approved is False
            or str(approved).strip().lower() in {"false", "0", "no", "rejected"}
            or "\u5ba1\u6838\u4e0d\u901a\u8fc7" in reason
        ):
            logger.warning(f"Image moderation rejected: {file_path}")
            return self.CATEGORY_FILTERED, [], "", self.CATEGORY_FILTERED, []

        category = data.get("category", "")
        tags = data.get("tags", [])
        description = self._sanitize_model_scalar(data.get("description", "emoji")) or "emoji"
        scenes = data.get("scenes", [])

        normalized_category = self._normalize_category(category)

        # 标签/场景规范化：保序去重 + 数量截断（tags≤4、scenes≤2，与提示词约束一致）
        tags = self.normalize_label_list(tags, MAX_TAGS)
        scenes = self.normalize_label_list(scenes, MAX_SCENES)

        return normalized_category, tags, description, normalized_category, scenes

    def _sanitize_model_scalar(self, value: Any) -> str:
        """Normalize single-value model outputs before category matching."""
        text = str(value or "").strip()
        text = text.strip("`")
        text = text.strip(" \t\r\n\"'")
        text = re.sub(r"^[\[\(\{<]+|[\]\)\}>]+$", "", text)
        text = text.rstrip("\u3002\uff01\uff0c\u3001\uff1b;\uff1a:")
        return text.strip()

    def _extract_json_payload(self, response: str) -> dict[str, Any] | None:
        """从 VLM 响应中提取第一个合法 JSON 对象。"""
        candidates: list[str] = []

        fenced_blocks = re.findall(r"```(?:json)?\s*(.*?)```", response, flags=re.DOTALL)
        candidates.extend(block.strip() for block in fenced_blocks if block.strip())
        candidates.append(response.strip())

        for candidate in candidates:
            parsed = self._try_parse_json_candidate(candidate)
            if parsed is not None:
                return parsed

        return None

    def _try_parse_json_candidate(self, text: str) -> dict[str, Any] | None:
        """解析候选文本中的 JSON 对象，兼容前后缀说明文字。"""
        decoder = json.JSONDecoder()

        for index, char in enumerate(text):
            if char != "{":
                continue
            try:
                parsed, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed

        return None

    def _parse_legacy_format(self, response: str) -> tuple[str, list[str], str, str, list[str]]:
        """兼容旧格式：管道符分隔的响应。"""
        # 处理审核不通过
        if self.CATEGORY_FILTERED in response or "审核不通过" in response:
            return self.CATEGORY_FILTERED, [], "", self.CATEGORY_FILTERED, []

        # 兼容旧格式：情绪分类|语义标签|画面描述|场景标签
        parts = [p.strip() for p in response.strip().split("|")]
        emotion_result = parts[0] if parts else ""
        tags_str = parts[1] if len(parts) > 1 else ""
        tags_result = [
            t.strip()
            for t in tags_str.replace("，", ",").replace("、", ",").split(",")
            if t.strip()
        ]
        desc_result = parts[2] if len(parts) > 2 else "表情包"
        scenes_str = parts[3] if len(parts) > 3 else ""
        scenes_result = [
            s.strip()
            for s in scenes_str.replace("，", ",").replace("、", ",").replace("；", ",").split(",")
            if s.strip()
        ]

        category = self._normalize_category(emotion_result)
        return (
            category,
            self.normalize_label_list(tags_result, MAX_TAGS),
            desc_result,
            category,
            self.normalize_label_list(scenes_result, MAX_SCENES),
        )
