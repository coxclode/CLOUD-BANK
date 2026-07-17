"""
Puerto de abstracción del proveedor LLM.

Permite intercambiar Anthropic / OpenAI / Gemini vía `CLOUDBANK_LLM_PROVIDER`
sin tocar la lógica de razonamiento de los Deep Agents (src/agents/deep/*).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


class LLMResponse(BaseModel):
    content: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str


class LLMTransientError(Exception):
    """Error transitorio del proveedor (rate limit, timeout, conexión) — reintentable."""


class LLMProviderPort(ABC):
    @abstractmethod
    async def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 1500,
        temperature: float = 0.0,
    ) -> LLMResponse:
        """Envía un prompt system+user y devuelve la respuesta normalizada."""
