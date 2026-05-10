from collections.abc import AsyncIterator

from groq import AsyncGroq

from .base import LLMProvider, LLMResponse

# Groq pricing is effectively free on the dev tier (May 2026).
# Update these when moving to a paid plan.
_INPUT_COST_PER_M = 0.0
_OUTPUT_COST_PER_M = 0.0


class GroqProvider(LLMProvider):
    """LLM provider using Groq inference API.

    Groq runs open-source models (LLaMA 3, Mixtral) with very low latency.
    The API is OpenAI-compatible, so the implementation mirrors OpenAIProvider.

    Recommended models:
        llama-3.3-70b-versatile  — best quality, good for editorial tasks
        llama-3.1-8b-instant     — fastest, good for simple classifications
        mixtral-8x7b-32768       — large context window (32k tokens)

    Required settings:
        GROQ_API_KEY
    """

    def __init__(self, api_key: str, default_model: str) -> None:
        self._client = AsyncGroq(api_key=api_key)
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
        # Groq does not support vision as of May 2026.
        raise NotImplementedError("GroqProvider does not support vision — use ClaudeProvider for visual analysis")

    async def stream(self, system: str, messages: list[dict]) -> AsyncIterator[str]:
        raise NotImplementedError

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * _INPUT_COST_PER_M + output_tokens * _OUTPUT_COST_PER_M) / 1_000_000
