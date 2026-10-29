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


class ModelError(HarnessError):
    code = "model_error"


class ModelAuthenticationError(ModelError):
    code = "model_authentication_error"


class ModelInvalidRequestError(ModelError):
    code = "model_invalid_request"


class ModelContentFilterError(ModelError):
    code = "model_content_filter"


class ModelTimeoutError(ModelError):
    code = "model_timeout"


class ModelProviderError(ModelError):
    code = "model_provider_error"


class ModelRateLimitError(ModelError):
    """The model provider throttled the request; callers may retry later."""

    code = "model_rate_limited"


class ActionLifecycleError(HarnessError):
    code = "action_lifecycle_error"


class ApprovalError(ActionLifecycleError):
    code = "approval_error"


class ActionInProgressError(ActionLifecycleError):
    code = "action_in_progress"


class ActionOutcomeUnknownError(ActionLifecycleError):
    code = "action_outcome_unknown"


class ConcurrencyError(ActionLifecycleError):
    code = "concurrency_error"


class AuditError(HarnessError):
    code = "audit_error"


class DuplicateEventError(AuditError):
    code = "duplicate_event"


class EventSequenceError(AuditError):
    code = "event_sequence_error"
