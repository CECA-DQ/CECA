import base64
from collections.abc import AsyncIterator

from openai import AsyncOpenAI

from .base import LLMProvider, LLMResponse

# gpt-4o pricing as of May 2026 (USD per 1M tokens)
_INPUT_COST_PER_M = 2.50
_OUTPUT_COST_PER_M = 10.00


class OpenAIProvider(LLMProvider):
    """LLM provider using the OpenAI API.

    Recommended models:
        gpt-4o           — best quality, supports vision
        gpt-4o-mini      — faster and cheaper for simple tasks

    Required settings:
        OPENAI_API_KEY
    """

    def __init__(self, api_key: str, default_model: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._default_model = default_model

    async def generate(
        self,
        system: str,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        response = await self._client.chat.completions.create(
            model=model or self._default_model,
            messages=[{"role": "system", "content": system}, *messages],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        usage = response.usage
        return LLMResponse(
            text=response.choices[0].message.content or "",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            model=response.model,
            cost=self.estimate_cost(
                usage.prompt_tokens if usage else 0,
                usage.completion_tokens if usage else 0,
            ),
        )

    async def generate_with_vision(
        self,
        system: str,
        messages: list[dict],
        images: list[bytes],
    ) -> LLMResponse:
        image_content = [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{base64.b64encode(img).decode()}"},
            }
            for img in images
        ]
        augmented = list(messages)
        if augmented and augmented[-1]["role"] == "user":
            last = augmented[-1]
            existing = last["content"] if isinstance(last["content"], list) else [{"type": "text", "text": last["content"]}]
            augmented[-1] = {"role": "user", "content": existing + image_content}
        else:
            augmented.append({"role": "user", "content": image_content})

        response = await self._client.chat.completions.create(
            model=self._default_model,
            messages=[{"role": "system", "content": system}, *augmented],
            max_tokens=2000,
        )
        usage = response.usage
        return LLMResponse(
            text=response.choices[0].message.content or "",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            model=response.model,
            cost=self.estimate_cost(
                usage.prompt_tokens if usage else 0,
                usage.completion_tokens if usage else 0,
            ),
        )

    async def stream(self, system: str, messages: list[dict]) -> AsyncIterator[str]:
        raise NotImplementedError

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * _INPUT_COST_PER_M + output_tokens * _OUTPUT_COST_PER_M) / 1_000_000
