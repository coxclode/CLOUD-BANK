from __future__ import annotations

import anthropic

from src.infrastructure.llm.provider_port import LLMProviderPort, LLMResponse, LLMTransientError


class AnthropicProvider(LLMProviderPort):
    def __init__(self, api_key: str, model: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=60, max_retries=0)
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
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except (anthropic.APIConnectionError, anthropic.RateLimitError) as exc:
            raise LLMTransientError(str(exc)) from exc

        text = ""
        for block in response.content:
            if block.type == "text":
                text = block.text
                break

        return LLMResponse(
            content=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=response.model,
            provider="anthropic",
        )
