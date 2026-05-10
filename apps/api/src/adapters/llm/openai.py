from collections.abc import AsyncIterator

from .base import LLMProvider, LLMResponse


class OpenAIProvider(LLMProvider):
    """Stub — implement when needed."""

    def __init__(self, api_key: str, default_model: str) -> None:
        self._api_key = api_key
        self._default_model = default_model

    async def generate(self, system, messages, model=None, temperature=0.7, max_tokens=2000) -> LLMResponse:
        raise NotImplementedError

    async def generate_with_vision(self, system, messages, images) -> LLMResponse:
        raise NotImplementedError

    async def stream(self, system, messages) -> AsyncIterator[str]:
        raise NotImplementedError

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        raise NotImplementedError
