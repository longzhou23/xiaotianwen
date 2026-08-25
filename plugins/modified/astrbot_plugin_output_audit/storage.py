"""Privacy-preserving, bounded audit storage."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Any


class AuditStorage:
    def __init__(self, data_dir: Path, retention_days: int) -> None:
        self._data_dir = data_dir
        self._db_path = data_dir / "output_audit.sqlite3"
        self._retention_seconds = max(1, retention_days) * 86400
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _initialize_sync(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS audits (
                    created_at REAL NOT NULL, request_id TEXT NOT NULL,
                    conversation_type TEXT NOT NULL, provider_id TEXT NOT NULL,
                    candidate_hash TEXT NOT NULL, candidate_length INTEGER NOT NULL,
                    decision TEXT NOT NULL, risk_level TEXT NOT NULL, categories TEXT NOT NULL,
                    reason_code TEXT NOT NULL, action TEXT NOT NULL, rewrote INTEGER NOT NULL,
                    fallback_used INTEGER NOT NULL, elapsed_ms INTEGER NOT NULL,
                    final_hash TEXT NOT NULL, final_length INTEGER NOT NULL, error_code TEXT NOT NULL
                )"""
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_audits_created_at ON audits(created_at)")
            connection.execute("DELETE FROM audits WHERE created_at < ?", (time.time() - self._retention_seconds,))

    async def record(self, row: dict[str, Any]) -> None:
        async with self._lock:
            await asyncio.to_thread(self._record_sync, row)

    def _record_sync(self, row: dict[str, Any]) -> None:
        values = {
            "created_at": time.time(),
            "request_id": "",
            "conversation_type": "unknown",
            "provider_id": "",
            "candidate_hash": "",
            "candidate_length": 0,
            "decision": "unavailable",
            "risk_level": "unknown",
            "categories": "[]",
            "reason_code": "",
            "action": "allow",
            "rewrote": 0,
            "fallback_used": 0,
            "elapsed_ms": 0,
            "final_hash": "",
            "final_length": 0,
            "error_code": "",
        }
        values.update(row)
        with sqlite3.connect(self._db_path) as connection:
            connection.execute(
                """INSERT INTO audits VALUES (
                    :created_at,:request_id,:conversation_type,:provider_id,:candidate_hash,
                    :candidate_length,:decision,:risk_level,:categories,:reason_code,:action,
                    :rewrote,:fallback_used,:elapsed_ms,:final_hash,:final_length,:error_code
                )""",
                values,
            )
