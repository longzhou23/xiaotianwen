from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from iris_memory.cognitive.contracts import Perspective
from iris_memory.cognitive.iris_adapter import CognitiveRuntime, IrisPostAdapter
from iris_memory.l2_memory.models import MemoryEntry, MemorySearchResult
from iris_memory.platform.base import ReplyInfo
import pytest


def test_post_adapter_projects_self_view_without_changing_raw_iris_memory():
    runtime = CognitiveRuntime()
    entry = MemoryEntry(
        id="memory:1",
        content="小天文曾经和 NICEICK 玩梗",
        metadata={
            "cognitive_runtime": {
                "subject_entity": "agent:xiaotianwen",
                "perspective": "autobiographical",
            }
        },
    )
    result = MemorySearchResult(entry=entry, score=1.0, distance=0.0)

    rendered = runtime.post_adapter.format_l2_context([result])

    assert entry.content == "小天文曾经和 NICEICK 玩梗"
    assert "[你的经历] 小天文曾经和 NICEICK 玩梗" in rendered


def test_post_adapter_leaves_ambiguous_legacy_assistant_label_unresolved():
    adapter = IrisPostAdapter(CognitiveRuntime().perspective)

    view = adapter.project_memory(
        memory_id="legacy:1",
        content="助手曾经和 NICEICK 玩梗",
        metadata={},
    )

    assert view.perspective is Perspective.UNRESOLVED
    assert view.content == view.raw_content


@pytest.mark.parametrize("memory_id", ["l1:runtime-view", "l2:runtime-view", "l3:runtime-view"])
def test_runtime_projection_contract_is_source_agnostic_and_never_rewrites_raw(memory_id):
    adapter = IrisPostAdapter(CognitiveRuntime().perspective)
    raw = "小天文曾经参与观测"
    view = adapter.project_memory(
        memory_id=memory_id,
        content=raw,
        metadata={"cognitive_runtime": {"subject_entity": "agent:xiaotianwen"}},
    )

    assert view.raw_content == raw
    assert view.content == raw
    assert view.perspective is Perspective.AUTOBIOGRAPHICAL


def test_pre_adapter_attaches_structured_metadata_without_rewriting_event_content():
    runtime = CognitiveRuntime()
    event = MagicMock()
    event.message_str = "龙洲你看这个"
    event.get_platform_name.return_value = "qq"
    event.get_self_id.return_value = "99999"

    platform = MagicMock()
    platform.get_raw_message.return_value = {"message_id": "42", "time": 1788288000}
    platform.get_user_id.return_value = "10001"
    platform.get_user_name.return_value = "龙洲"
    platform.get_reply_info.return_value = ReplyInfo()
    platform.get_mentioned_users.return_value = [("10002", "longz")]
    platform.get_session_id.return_value = "123"
    platform.is_group_message.return_value = True

    with patch("iris_memory.platform.get_adapter", return_value=platform):
        result = runtime.pre_adapter.attach(event)

    assert event.message_str == "龙洲你看这个"
    assert result.metadata["subject_entity"] == "person:qq:10001"
    assert result.metadata["resolved_entities"] == ("person:qq:10002",)
    assert event.set_extra.call_args.args[0] == "iris_cognitive_preprocess"


def test_pre_adapter_binds_self_only_from_explicit_event_uid_not_label_text():
    runtime = CognitiveRuntime()
    event = MagicMock()
    event.message_str = "小天文以前说过什么"
    event.get_platform_name.return_value = "qq"
    event.get_self_id.return_value = "99999"
    platform = MagicMock()
    platform.get_raw_message.return_value = {"message_id": "self-42"}
    platform.get_user_id.return_value = "10001"
    platform.get_user_name.return_value = "普通用户"
    platform.get_reply_info.return_value = ReplyInfo()
    platform.get_mentioned_users.return_value = [("99999", "小天文")]
    platform.get_session_id.return_value = "123"
    platform.is_group_message.return_value = True

    with patch("iris_memory.platform.get_adapter", return_value=platform):
        result = runtime.pre_adapter.preprocess_event(event)

    assert result.experience.subject.entity_id == "person:qq:10001"
    assert result.experience.event.mentioned_entities[0].entity_id == "agent:xiaotianwen"
    assert result.experience.event.mentioned_entities[0].source == "event_self_uid"
    assert result.metadata["identity_diagnostic"] == {
        "self_uid_observed": True,
        "self_mention_uid_matched": True,
        "self_reply_uid_matched": False,
    }


def test_canonical_message_id_handles_int_prefixed_and_empty_values():
    from iris_memory.cognitive.iris_adapter import _canonical_message_id

    assert _canonical_message_id("qq", 42) == "qq:42"
    assert _canonical_message_id("qq", "42") == "qq:42"
    assert _canonical_message_id("qq", "qq:42") == "qq:42"
    assert _canonical_message_id("qq", "") == ""


def test_pre_adapter_reply_event_id_matches_canonical_event_namespace():
    runtime = CognitiveRuntime()
    event = MagicMock()
    event.message_str = "小天文，继续"
    event.get_platform_name.return_value = "qq"
    event.get_self_id.return_value = "99999"
    platform = MagicMock()
    platform.get_raw_message.return_value = {"message_id": 42, "time": 1788288000}
    platform.get_user_id.return_value = "10001"
    platform.get_user_name.return_value = "普通用户"
    platform.get_reply_info.return_value = ReplyInfo(message_id=41, user_id="10001", user_name="普通用户")
    platform.get_mentioned_users.return_value = []
    platform.get_session_id.return_value = "123"
    platform.is_group_message.return_value = False

    with patch("iris_memory.platform.get_adapter", return_value=platform):
        result = runtime.pre_adapter.preprocess_event(event)

    assert result.experience.event.event_id == "qq:42"
    assert result.experience.event.raw_metadata["reply_event_id"] == "qq:41"
