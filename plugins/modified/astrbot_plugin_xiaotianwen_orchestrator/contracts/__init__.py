"""跨插件共享的、与 AstrBot 平台对象无关的协议模型。"""

from .context import ContextSection
from .media import MediaRef
from .tools import ToolExecutionPolicy
from .turn import ALLOWED_ROUTES, TurnEnvelope
from .validation import ContractValidationError

__all__ = [
    "ALLOWED_ROUTES",
    "ContextSection",
    "ContractValidationError",
    "MediaRef",
    "ToolExecutionPolicy",
    "TurnEnvelope",
]
