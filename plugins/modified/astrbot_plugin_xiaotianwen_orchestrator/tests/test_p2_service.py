from __future__ import annotations

from astrbot_plugin_xiaotianwen_orchestrator.p2 import OrchestratorService


def test_service_boundary_exposes_read_only_status_without_web_manager_imports() -> None:
    service = OrchestratorService()
    service.ingest(
        {"message_id": "m-service", "user_id": "u-service", "group_id": "g-service", "raw_message": "状态"},
        now=1.0,
    )
    snapshot = service.snapshot()
    assert snapshot.pending_turns == 1
    assert snapshot.active_timer_count == 0
    assert snapshot.assembler_owner == "xiaotianwen_context_assembler"
    assert "web" not in repr(snapshot).lower()
