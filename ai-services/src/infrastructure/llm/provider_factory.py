from __future__ import annotations

from src.core.config import AppSettings
from src.infrastructure.llm.provider_port import LLMProviderPort


def get_llm_provider(settings: AppSettings) -> LLMProviderPort:
    """
    Construye el proveedor LLM activo según `CLOUDBANK_LLM_PROVIDER` (LLMSettings.provider).
    Cambiar de proveedor es solo una variable de entorno — los Deep Agents (src/agents/deep/*)
    dependen únicamente de LLMProviderPort.complete(), nunca del SDK concreto.
    """
    provider = settings.llm.provider

    if provider == "anthropic":
        from src.infrastructure.llm.anthropic_provider import AnthropicProvider
        api_key = settings.anthropic_api_key.get_secret_value()
        if not api_key:
            raise ValueError("CLOUDBANK_ANTHROPIC_API_KEY no está configurado (LLM_PROVIDER=anthropic).")
        return AnthropicProvider(api_key=api_key, model=settings.llm.primary_model)

    if provider == "openai":
        from src.infrastructure.llm.openai_provider import OpenAIProvider
        api_key = settings.openai_api_key.get_secret_value()
        if not api_key:
            raise ValueError("CLOUDBANK_OPENAI_API_KEY no está configurado (LLM_PROVIDER=openai).")
        return OpenAIProvider(api_key=api_key, model=settings.llm.primary_model)

    if provider == "gemini":
        from src.infrastructure.llm.gemini_provider import GeminiProvider
        api_key = settings.gemini_api_key.get_secret_value()
        if not api_key:
            raise ValueError("CLOUDBANK_GEMINI_API_KEY no está configurado (LLM_PROVIDER=gemini).")
        return GeminiProvider(api_key=api_key, model=settings.llm.primary_model)

    raise ValueError(f"Proveedor LLM no soportado: '{provider}'. Usa anthropic | openai | gemini.")
