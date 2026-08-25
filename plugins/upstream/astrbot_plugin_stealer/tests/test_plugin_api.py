"""PR #90: PluginAPI 待审核分类列表构建（_build_categories_list）。"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from astrbot_plugin_stealer.plugin_api import PluginAPI


def _build_api(category_info):
    cfg = types.SimpleNamespace(get_category_info=lambda: category_info)
    plugin = types.SimpleNamespace(plugin_config=cfg)
    return PluginAPI(plugin)


class TestBuildCategoriesList:
    def test_known_categories_with_zero_counts(self):
        api = _build_api(
            [
                {"key": "happy", "name": "开心", "desc": "快乐"},
                {"key": "angry", "name": "生气", "desc": "愤怒"},
            ]
        )
        result = api._build_categories_list({"happy": 3, "angry": 1})
        assert result == [
            {"key": "happy", "name": "开心", "count": 3},
            {"key": "angry", "name": "生气", "count": 1},
        ]

    def test_unknown_category_in_counts_is_appended(self):
        api = _build_api([{"key": "happy", "name": "开心", "desc": "快乐"}])
        result = api._build_categories_list({"happy": 2, "custom_x": 5})
        keys = [item["key"] for item in result]
        assert "custom_x" in keys
        custom = next(item for item in result if item["key"] == "custom_x")
        assert custom == {"key": "custom_x", "name": "custom_x", "count": 5}

    def test_sorted_by_count_desc(self):
        api = _build_api(
            [
                {"key": "happy", "name": "开心", "desc": ""},
                {"key": "sad", "name": "难过", "desc": ""},
                {"key": "angry", "name": "生气", "desc": ""},
            ]
        )
        result = api._build_categories_list({"happy": 1, "sad": 9, "angry": 4})
        assert [item["key"] for item in result] == ["sad", "angry", "happy"]
        assert [item["count"] for item in result] == [9, 4, 1]

    def test_empty_counts_returns_known_categories(self):
        api = _build_api([{"key": "happy", "name": "开心", "desc": ""}])
        result = api._build_categories_list({})
        assert result == [{"key": "happy", "name": "开心", "count": 0}]

    def test_empty_category_info_returns_counts_only(self):
        api = _build_api([])
        result = api._build_categories_list({"a": 2, "b": 1})
        assert result == [
            {"key": "a", "name": "a", "count": 2},
            {"key": "b", "name": "b", "count": 1},
        ]