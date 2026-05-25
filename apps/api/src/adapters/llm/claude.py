import base64
from collections.abc import AsyncIterator

from anthropic import AsyncAnthropic

from .base import LLMProvider, LLMResponse

# claude-sonnet-4-6 pricing per million tokens (May 2026)
_INPUT_COST_PER_M = 3.0
_OUTPUT_COST_PER_M = 15.0

_VISION_MODEL = "claude-sonnet-4-6"


class ClaudeProvider(LLMProvider):
    def __init__(self, api_key: str, default_model: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._default_model = default_model

    async def generate(
        self,
        system: str,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        response = await self._client.messages.create(
            model=model or self._default_model,
            system=system,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return LLMResponse(
            text=response.content[0].text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=response.model,
            cost=self.estimate_cost(response.usage.input_tokens, response.usage.output_tokens),
        )

    async def generate_with_vision(
        self,
        system: str,
        messages: list[dict],
        images: list[bytes],
    ) -> LLMResponse:
        """Send images alongside the last user message using Claude's vision API."""
        image_blocks = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(img).decode(),
                },
            }
            for img in images
        ]

        augmented = list(messages)
        if augmented and augmented[-1]["role"] == "user":
            existing = augmented[-1]["content"]
            text_block = (
                existing
                if isinstance(existing, list)
                else [{"type": "text", "text": existing}]
            )
            augmented[-1] = {"role": "user", "content": text_block + image_blocks}
        else:
            augmented.append({"role": "user", "content": image_blocks})

        response = await self._client.messages.create(
            model=_VISION_MODEL,
            system=system,
            messages=augmented,
            max_tokens=2000,
        )
        return LLMResponse(
            text=response.content[0].text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=response.model,
            cost=self.estimate_cost(response.usage.input_tokens, response.usage.output_tokens),
        )

    async def stream(self, system: str, messages: list[dict]) -> AsyncIterator[str]:
        raise NotImplementedError

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * _INPUT_COST_PER_M + output_tokens * _OUTPUT_COST_PER_M) / 1_000_000
