"""Contract tests for the local OneBot replacement transport."""

from __future__ import annotations

import json

import pytest

from tests.ui.server.onebot_reverse import OneBotBridgeError, OneBotReverseBridge, _client_frame, _validate_ws_url, load_onebot_bridge_settings


def test_reverse_url_is_loopback_only() -> None:
    assert _validate_ws_url("ws://127.0.0.1:8001/ws") == ("127.0.0.1", 8001, "/ws")
    with pytest.raises(OneBotBridgeError):
        _validate_ws_url("ws://example.invalid:8001/ws")
    with pytest.raises(OneBotBridgeError):
        _validate_ws_url("ws://127.0.0.1:8001/ws?token=secret")


def test_client_frames_are_masked_and_bridge_acknowledges_actions() -> None:
    frame = _client_frame(b"synthetic")
    assert frame[1] & 0x80
    bridge = OneBotReverseBridge(url="ws://127.0.0.1:1/ws", token="local-test-token")
    response = bridge._response_for_action({"action": "send_private_msg", "params": {}, "echo": {"seq": 1}})
    assert response["status"] == "ok"
    assert response["data"]["message_id"] == 1
    assert response["echo"] == {"seq": 1}


def test_bridge_settings_read_only_from_data_copy(tmp_path) -> None:
    (tmp_path / "cmd_config.json").write_text(
        json.dumps({"platform": [{"type": "aiocqhttp", "ws_reverse_port": 6403, "ws_reverse_token": "local-test-token"}]}),
        encoding="utf-8",
    )
    settings = load_onebot_bridge_settings(tmp_path)
    assert settings["url"] == "ws://127.0.0.1:6403/ws"
    assert settings["token"] == "local-test-token"
