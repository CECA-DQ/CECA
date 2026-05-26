from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    cost: float


class LLMProvider(ABC):
    """Abstract interface for LLM providers.

    Implementations live alongside this file (claude.py, openai.py).
    Use get_llm_provider() in factory.py to obtain an instance.
    """

    @abstractmethod
    async def generate(
        self,
        system: str,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> LLMResponse: ...

    @abstractmethod
    async def generate_with_vision(
        self,
        system: str,
        messages: list[dict],
        images: list[bytes],
    ) -> LLMResponse: ...

    async def generate_with_interleaved_content(
        self,
        system: str,
        content: list[dict],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4000,
    ) -> LLMResponse:
        """Send a prompt where text and images are interleaved.

        content is a list of dicts with either:
          {"type": "text",  "text": "..."}
          {"type": "image", "data": bytes, "mime_type": "image/jpeg"}

        Default implementation raises NotImplementedError.
        Gemini adapter overrides this for frame-scoring use cases.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support interleaved content. "
            "Use GeminiProvider for visual scoring."
        )

    @abstractmethod
    async def stream(
        self,
        system: str,
        messages: list[dict],
    ) -> AsyncIterator[str]: ...

    @abstractmethod
    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float: ...
