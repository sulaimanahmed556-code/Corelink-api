"""
Ollama Provider

Default AI provider — runs locally with Llama3 (or any pulled model).
No API key required. Falls back gracefully if Ollama is not running.
"""

import httpx
from loguru import logger

from app.services.ai.base import AIProvider, AIResponse


class OllamaProvider(AIProvider):
    """
    Local LLM via Ollama HTTP API.

    Default model: llama3 (auto-pulled by Ollama on first use).
    Configure base_url and model via environment if needed.
    """

    def __init__(self, base_url: str = "http://localhost:11434", model: str = "llama3"):
        self.base_url = base_url.rstrip("/")
        self.model = model

    async def complete(
        self,
        system: str,
        user: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> AIResponse:
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": user},
                        ],
                        "options": {
                            "temperature": temperature,
                            "num_predict": max_tokens,
                        },
                        "stream": False,
                    },
                )
                response.raise_for_status()
                data = response.json()
                text = data["message"]["content"].strip()
                return AIResponse(text=text, provider="ollama", model=self.model)

        except Exception as exc:
            logger.warning(f"Ollama completion failed: {exc}")
            raise

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self.base_url}/api/tags")
                return r.status_code == 200
        except Exception:
            return False
