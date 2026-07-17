from __future__ import annotations

import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted, ServiceUnavailable

from src.infrastructure.llm.provider_port import LLMProviderPort, LLMResponse, LLMTransientError


class GeminiProvider(LLMProviderPort):
    def __init__(self, api_key: str, model: str) -> None:
        genai.configure(api_key=api_key)
        self._model_name = model

    async def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 1500,
        temperature: float = 0.0,
    ) -> LLMResponse:
        model = genai.GenerativeModel(self._model_name, system_instruction=system)
        try:
            response = await model.generate_content_async(
                user,
                generation_config={
                    "max_output_tokens": max_tokens,
                    "temperature": temperature,
                },
            )
        except (ResourceExhausted, ServiceUnavailable) as exc:
            raise LLMTransientError(str(exc)) from exc

        usage = getattr(response, "usage_metadata", None)
        return LLMResponse(
            content=response.text or "",
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            model=self._model_name,
            provider="gemini",
        )
