"""标准库级别的契约校验和脱敏指纹辅助函数。

这个文件不能依赖 AstrBot 或任何具体平台。协议层只接收普通 Python 值，
以便在插件、回放工具与离线测试之间共享。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, TypeAlias


class ContractValidationError(ValueError):
    """输入不能安全地进入跨插件协议时抛出。"""


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,191}$")
_SOURCE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


def require_non_empty_string(value: object, field_name: str) -> str:
    """Return a non-empty string without silently coercing platform values."""

    if not isinstance(value, str):
        raise ContractValidationError(f"{field_name} must be a string")
    result = value.strip()
    if not result:
        raise ContractValidationError(f"{field_name} must not be empty")
    return result


def require_identifier(value: object, field_name: str) -> str:
    result = require_non_empty_string(value, field_name)
    if not _IDENTIFIER_RE.fullmatch(result):
        raise ContractValidationError(
            f"{field_name} contains unsupported characters or is too long"
        )
    return result


def require_source_name(value: object, field_name: str = "source") -> str:
    result = require_non_empty_string(value, field_name)
    if not _SOURCE_RE.fullmatch(result):
        raise ContractValidationError(
            f"{field_name} contains unsupported characters or is too long"
        )
    return result


def require_optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return require_non_empty_string(value, field_name)


def require_non_negative_int(value: object, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise ContractValidationError(f"{field_name} must be a non-negative integer")
    return value


def require_positive_int(value: object, field_name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ContractValidationError(f"{field_name} must be a positive integer")
    return value


def require_finite_timestamp(value: object, field_name: str) -> float:
    if type(value) not in (int, float):
        raise ContractValidationError(f"{field_name} must be a finite timestamp")
    result = float(value)
    if result < 0 or not math.isfinite(result):
        raise ContractValidationError(f"{field_name} must be a finite non-negative timestamp")
    return result


def require_bool(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        raise ContractValidationError(f"{field_name} must be a boolean")
    return value


def ensure_json_value(value: object, field_name: str = "value") -> JsonValue:
    """Validate and copy JSON-compatible data.

    A raw OneBot/AstrBot event is intentionally rejected instead of being
    coerced with ``str(value)``. Coercion would make hidden platform state look
    serializable and can accidentally persist credentials or message objects.
    """

    if value is None or type(value) in (str, bool, int):
        return value  # type: ignore[return-value]
    if type(value) is float:
        if not math.isfinite(value):
            raise ContractValidationError(f"{field_name} must not contain NaN or infinity")
        return value
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ContractValidationError(f"{field_name} mapping keys must be strings")
            result[key] = ensure_json_value(nested_value, f"{field_name}.{key}")
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            ensure_json_value(nested_value, f"{field_name}[{index}]")
            for index, nested_value in enumerate(value)
        ]
    raise ContractValidationError(
        f"{field_name} contains a non-JSON platform or runtime object: "
        f"{type(value).__name__}"
    )


def canonical_json(value: JsonValue) -> str:
    """Produce one stable JSON representation suitable for a fingerprint."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def structural_fingerprint(value: JsonValue) -> str:
    """Hash a JSON-safe structure without introducing logging side effects."""

    return sha256_text(canonical_json(value))
