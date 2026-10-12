"""Define normalized errors raised across the harness."""

from __future__ import annotations


class HarnessError(Exception):
    """Base error carrying a stable machine-readable code."""

    code = "harness_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ConfigurationError(HarnessError):
    code = "configuration_error"


class RegistrationError(HarnessError):
    code = "registration_error"


class DuplicateRegistrationError(RegistrationError):
    code = "duplicate_registration"


class UnknownRegistrationError(RegistrationError):
    code = "unknown_registration"


class AuthorizationError(HarnessError):
    code = "authorization_error"


class InputValidationError(HarnessError):
    code = "input_validation_error"


class ExecutionLimitError(HarnessError):
    code = "execution_limit_error"


class ExecutionTimeoutError(HarnessError):
    code = "execution_timeout"


class ToolExecutionError(HarnessError):
    code = "tool_execution_error"


class AuditError(HarnessError):
    code = "audit_error"


class DuplicateEventError(AuditError):
    code = "duplicate_event"


class EventSequenceError(AuditError):
    code = "event_sequence_error"
