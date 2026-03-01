"""
AI Provider Factory

Selects the configured LLM provider at startup.
If no cloud provider is configured (no API key), defaults to Ollama.

Priority:
1. OpenAI  — if OPENAI_API_KEY is set and non-placeholder
2. Ollama  — always available as the default/fallback
"""

from __future__ import annotations

from loguru import logger

from app.services.ai.base import AIProvider, AIResponse
from app.services.ai.ollama_provider import OllamaProvider
from app.services.ai.openai_provider import OpenAIProvider


_PLACEHOLDER_KEYS = {"", "your-key-here", "sk-placeholder", "none", "null"}


def _is_real_key(value: str | None) -> bool:
    if not value:
        return False
    return value.strip().lower() not in _PLACEHOLDER_KEYS


def build_provider() -> AIProvider:
    """
    Instantiate the best available AI provider.

    Reads from app.config.settings so it picks up the .env values at runtime.
    """
    try:
        from app.config import settings

        # 1. OpenAI
        openai_key = getattr(settings, "OPENAI_API_KEY", None)
        openai_model = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")
        if _is_real_key(openai_key):
            logger.info(f"AI provider: OpenAI ({openai_model})")
            return OpenAIProvider(api_key=openai_key, model=openai_model)

    except Exception as exc:
        logger.warning(f"Could not load settings for AI provider selection: {exc}")

    # Default: Ollama (local, no API key needed)
    ollama_url = "http://localhost:11434"
    ollama_model = "llama3"
    try:
        from app.config import settings
        ollama_url = getattr(settings, "OLLAMA_BASE_URL", ollama_url)
        ollama_model = getattr(settings, "OLLAMA_MODEL", ollama_model)
    except Exception:
        pass

    logger.info(f"AI provider: Ollama ({ollama_model} @ {ollama_url})")
    return OllamaProvider(base_url=ollama_url, model=ollama_model)


class FallbackAIProvider(AIProvider):
    """
    Wraps a primary provider with an Ollama fallback.

    If the primary provider fails, falls back to Ollama.
    If Ollama also fails, returns a stub response so nothing crashes.
    """

    def __init__(self, primary: AIProvider, fallback: OllamaProvider):
        self._primary = primary
        self._fallback = fallback

    async def complete(
        self,
        system: str,
        user: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> AIResponse:
        # Try primary
        try:
            result = await self._primary.complete(system, user, temperature, max_tokens)
            return result
        except Exception as primary_err:
            logger.warning(f"Primary AI provider failed ({primary_err}), falling back to Ollama")

        # Try Ollama fallback
        try:
            result = await self._fallback.complete(system, user, temperature, max_tokens)
            result.fallback_used = True
            return result
        except Exception as fallback_err:
            logger.error(f"Ollama fallback also failed: {fallback_err}")
            # Last resort stub
            return AIResponse(
                text="Summary temporarily unavailable.",
                provider="stub",
                model="none",
                fallback_used=True,
            )

    async def health_check(self) -> bool:
        try:
            return await self._primary.health_check()
        except Exception:
            return await self._fallback.health_check()


def get_ai_provider() -> AIProvider:
    """
    Module-level factory called by services.

    Returns a FallbackAIProvider: primary (OpenAI if configured) wrapped with
    an Ollama safety net.
    """
    primary = build_provider()
    ollama_fallback = OllamaProvider()

    # If primary IS already Ollama, don't double-wrap
    if isinstance(primary, OllamaProvider):
        return primary

    return FallbackAIProvider(primary=primary, fallback=ollama_fallback)


# Singleton — created once per process
_ai_provider: AIProvider | None = None


def ai() -> AIProvider:
    """Get (or lazily create) the global AI provider singleton."""
    global _ai_provider
    if _ai_provider is None:
        _ai_provider = get_ai_provider()
    return _ai_provider
