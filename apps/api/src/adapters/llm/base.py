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

    @abstractmethod
    async def stream(
        self,
        system: str,
        messages: list[dict],
    ) -> AsyncIterator[str]: ...

    @abstractmethod
    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float: ...
