"""P2 policy kernels for the staged refactor.

Every object in this package is platform-neutral.  It describes ownership,
effects and safety decisions; it does not call AstrBot, QQ, a Provider or a
filesystem.  Production integration remains an explicit later gate.
"""

from .affection import (
    BackgroundTaskRegistry,
    EmotionObservation,
    EmotionObservationLedger,
    ProviderBinding,
    ProviderResolution,
    classify_provider_result,
)
from .experiments import ExperimentLedger, ExperimentObservation, ExperimentSpec
from .hook_contract import HookContract, default_hook_contracts, validate_hook_contracts
from .operations import (
    BackupManifest,
    HealthState,
    RetryPolicy,
    ServiceHealthSnapshot,
    SQLiteBackupSet,
)
from .performance import (
    CanaryPlan,
    LongRunObservationTemplate,
    PerformanceSample,
    PerformanceSummary,
    compare_performance,
    default_long_run_templates,
    summarize_performance,
)
from .provider_registry import (
    ContextProviderRegistry,
    ProviderCollection,
    ProviderFailure,
    ProviderRegistration,
)
from .service import OrchestratorService, ServiceSnapshot
from .security import (
    GatedOutput,
    InputAssessment,
    PermissionDecision,
    SecurityBoundary,
)
from .tools import (
    ToolCall,
    ToolExecutionResult,
    ToolExecutor,
    ToolRegistry,
    ToolSpec,
    trim_tool_result,
)

__all__ = [
    "BackupManifest",
    "BackgroundTaskRegistry",
    "ContextProviderRegistry",
    "CanaryPlan",
    "EmotionObservation",
    "EmotionObservationLedger",
    "ExperimentLedger",
    "ExperimentObservation",
    "ExperimentSpec",
    "GatedOutput",
    "HealthState",
    "HookContract",
    "InputAssessment",
    "LongRunObservationTemplate",
    "PermissionDecision",
    "PerformanceSample",
    "PerformanceSummary",
    "ProviderBinding",
    "ProviderCollection",
    "ProviderFailure",
    "ProviderRegistration",
    "ProviderResolution",
    "OrchestratorService",
    "RetryPolicy",
    "SQLiteBackupSet",
    "SecurityBoundary",
    "ServiceSnapshot",
    "ServiceHealthSnapshot",
    "ToolCall",
    "ToolExecutionResult",
    "ToolExecutor",
    "ToolRegistry",
    "ToolSpec",
    "classify_provider_result",
    "compare_performance",
    "default_long_run_templates",
    "default_hook_contracts",
    "trim_tool_result",
    "summarize_performance",
    "validate_hook_contracts",
]
