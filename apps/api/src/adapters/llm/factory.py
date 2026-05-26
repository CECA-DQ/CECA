from src.config import settings

from .base import LLMProvider
from .claude import ClaudeProvider
from .gemini import GeminiProvider
from .groq import GroqProvider
from .openai import OpenAIProvider


def get_llm_provider() -> LLMProvider:
    match settings.llm_provider:
        case "claude":
            return ClaudeProvider(
                api_key=settings.anthropic_api_key,
                default_model=settings.llm_default_model,
            )
        case "groq":
            return GroqProvider(
                api_key=settings.groq_api_key,
                default_model=settings.llm_default_model,
            )
        case "openai":
            return OpenAIProvider(
                api_key=settings.openai_api_key,
                default_model="gpt-4o",
            )
        case "gemini":
            return GeminiProvider(
                api_key=settings.gemini_api_key,
                default_model=settings.llm_default_model or "gemini-2.0-flash",
            )
        case _:
            raise ValueError(f"Unknown LLM provider: {settings.llm_provider!r}")


def get_visual_analysis_provider() -> GeminiProvider:
    """Always returns a Gemini provider for visual frame scoring.

    Visual scoring requires interleaved text+image content which only
    Gemini supports in the current adapter stack.
    """
    return GeminiProvider(
        api_key=settings.gemini_api_key,
        default_model=settings.visual_analysis_model,
    )
