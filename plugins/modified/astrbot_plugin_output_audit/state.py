"""Small request state container kept separate for deterministic unit tests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RequestState:
    candidate_hash: str
    review_count: int = 0
    rewrite_count: int = 0
    finalized: bool = False
    final_text_hash: str = ""

