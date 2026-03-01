"""
AI Provider Base Strategy

Abstract base for all LLM providers. Concrete implementations
live in their own modules. The factory picks the right one based
on what's configured in settings.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class AIResponse:
    text: str
    provider: str
    model: str
    fallback_used: bool = False


class AIProvider(ABC):
    """Abstract LLM provider."""

    @abstractmethod
    async def complete(
        self,
        system: str,
        user: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> AIResponse:
        """Send a single system+user prompt and return the response text."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the provider is reachable and functional."""
        ...
