"""Base classes and data structures for the Lilith tool system."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolResult:
    """Result dataclass for tool execution outcomes.

    Attributes:
        success: Whether the tool execution succeeded.
        data: The result payload (any type).
        error: Error message if execution failed.

    """

    success: bool
    data: Any
    error: str = ""


class BaseTool(ABC):
    """Abstract base class for all Lilith tools.

    Subclasses must set ``name``, ``description``, and ``parameters``
    class attributes and implement the :meth:`execute` method.
    """

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] | None = None

    @abstractmethod
    def execute(self, **kwargs: Any) -> ToolResult:
        """Ejecutar la herramienta con los argumentos dados."""

    def validate(self, params: dict[str, Any]) -> bool:
        """Validar que los parámetros requeridos estén presentes."""
        if not self.parameters:
            return True
        for key, config in self.parameters.items():
            if config.get("required", False) and key not in params:
                return False
        return True
