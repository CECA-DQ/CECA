from src.config import settings

from .base import LLMProvider
from .claude import ClaudeProvider
from .openai import OpenAIProvider


def get_llm_provider() -> LLMProvider:
    match settings.llm_provider:
        case "claude":
            return ClaudeProvider(
                api_key=settings.anthropic_api_key,
                default_model=settings.llm_default_model,
            )
        case "openai":
            return OpenAIProvider(
                api_key=settings.openai_api_key,
                default_model="gpt-4o",
            )
        case _:
            raise ValueError(f"Unknown LLM provider: {settings.llm_provider!r}")
