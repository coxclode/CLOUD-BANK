from __future__ import annotations

import openai

from src.infrastructure.llm.provider_port import LLMProviderPort, LLMResponse, LLMTransientError


class OpenAIProvider(LLMProviderPort):
    def __init__(self, api_key: str, model: str) -> None:
        self._client = openai.AsyncOpenAI(api_key=api_key, timeout=60, max_retries=0)
        self._model = model

    async def complete(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 1500,
        temperature: float = 0.0,
    ) -> LLMResponse:
        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        except (openai.APIConnectionError, openai.RateLimitError) as exc:
            raise LLMTransientError(str(exc)) from exc

        return LLMResponse(
            content=response.choices[0].message.content or "",
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
            model=response.model,
            provider="openai",
        )
