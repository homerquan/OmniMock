from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn


@dataclass(frozen=True, slots=True)
class ErrorContext:
    code: str
    public_message: str
    detail: str = ""
    source: str | None = None


class OmniMockError(Exception):
    """Stable, safe error boundary for the application and adapters."""

    category = "omnimock"

    def __init__(self, context: ErrorContext) -> None:
        super().__init__(context.public_message)
        self.context = context


class ConfigurationError(OmniMockError):
    category = "configuration"


class ContractError(OmniMockError):
    category = "contract"


class ValidationError(OmniMockError):
    category = "validation"


class BehaviorResolutionError(OmniMockError):
    category = "behavior"


class StateConflictError(OmniMockError):
    category = "state"


class SecurityPolicyError(OmniMockError):
    category = "security"


class RuntimeLifecycleError(OmniMockError):
    category = "runtime"


def fail(error_type: type[OmniMockError], code: str, message: str, detail: str = "", source: str | None = None) -> NoReturn:
    raise error_type(ErrorContext(code, message, detail, source))
