"""Abstract base class for Claude runtimes."""
from abc import ABC, abstractmethod


class ClaudeRuntime(ABC):
    """Common interface for SDK and CLI runtimes."""

    @abstractmethod
    def call(self, user_message: str, user_id: int) -> str:
        """Send a message and return Claude's text response."""

    @abstractmethod
    def reset(self, user_id: int) -> None:
        """Clear conversation history for a user."""
