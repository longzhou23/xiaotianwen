"""Read-only context adapters and deterministic shadow assembly."""

from .assembler import ContextAssembler, ContextAssemblyResult, PayloadStructuralDiff
from .budgets import ContextAssemblyPolicy, DEFAULT_SOURCE_PRIORITIES
from .providers import (
    ContextAwareAdapter,
    ImageContextPoolAdapter,
    IrisMemoryAdapter,
    SharedContextAdapter,
)
from .memory import (
    AsyncSingleFlightCache,
    MemoryBudgetPolicy,
    MemoryQueryKey,
    TurnContextMemo,
    deduplicate_relationship_sections,
    is_low_information,
    normalize_memory_query,
)

__all__ = [
    "ContextAssembler",
    "ContextAssemblyPolicy",
    "ContextAssemblyResult",
    "ContextAwareAdapter",
    "DEFAULT_SOURCE_PRIORITIES",
    "ImageContextPoolAdapter",
    "IrisMemoryAdapter",
    "AsyncSingleFlightCache",
    "MemoryBudgetPolicy",
    "MemoryQueryKey",
    "PayloadStructuralDiff",
    "SharedContextAdapter",
    "TurnContextMemo",
    "deduplicate_relationship_sections",
    "is_low_information",
    "normalize_memory_query",
]
