"""Custom exception hierarchy for the Lilith agent ecosystem."""


class LilithError(Exception):
    """Base exception for all Lilith errors."""


class ToolError(LilithError):
    """Exception raised when a tool execution fails."""


class LLMError(LilithError):
    """Exception raised when communication with an LLM provider fails."""
