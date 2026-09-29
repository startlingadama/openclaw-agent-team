"""Explicit failure categories (REQUIREMENTS section 23)."""


class OpenClawError(Exception):
    """Base class for every explicit failure raised by the system."""

    def __init__(self, message: str = "", *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class ToolError(OpenClawError): ...


class LLMError(OpenClawError): ...


class AuthenticationError(OpenClawError): ...


class AuthorizationError(OpenClawError): ...


class ValidationError(OpenClawError): ...


class SkillError(OpenClawError): ...


class AgentError(OpenClawError): ...


class TaskError(OpenClawError): ...


class ApprovalError(OpenClawError): ...


class TeamError(OpenClawError): ...


class HistoryError(OpenClawError): ...
