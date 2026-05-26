"""Google Gemini adapter — vision-capable LLM provider.

Used primarily for the visual frame-scoring step (generate_with_interleaved_content)
where text and JPEG images must be interleaved in a single prompt.
Text-only calls (generate) are also supported for headline / highlight steps.
"""

import logging
from collections.abc import AsyncIterator

from google import genai
from google.genai import types

from .base import LLMProvider, LLMResponse

logger = logging.getLogger(__name__)

# Approximate cost per 1M tokens for Gemini 2.0 Flash (free tier has no cost,
# paid tier is $0.075 input / $0.30 output as of 2025-05).
_COST_INPUT_PER_M  = 0.075 / 1_000_000
_COST_OUTPUT_PER_M = 0.30  / 1_000_000


class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str, default_model: str = "gemini-2.0-flash"):
        self._client = genai.Client(api_key=api_key)
        self._default_model = default_model

    # ------------------------------------------------------------------
    # Text generation
    # ------------------------------------------------------------------

    async def generate(
        self,
        system: str,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        mdl = model or self._default_model
        user_text = "\n\n".join(m["content"] for m in messages if m.get("role") == "user")

        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        response = await self._client.aio.models.generate_content(
            model=mdl,
            contents=user_text,
            config=config,
        )
        text = response.text or ""
        usage = response.usage_metadata or {}
        in_tok  = getattr(usage, "prompt_token_count",     0) or 0
        out_tok = getattr(usage, "candidates_token_count", 0) or 0
        return LLMResponse(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            model=mdl,
            cost=self.estimate_cost(in_tok, out_tok),
        )

    # ------------------------------------------------------------------
    # Interleaved text + image content (visual scoring)
    # ------------------------------------------------------------------

    async def generate_with_interleaved_content(
        self,
        system: str,
        content: list[dict],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4000,
    ) -> LLMResponse:
        """Send a prompt with interleaved text and JPEG image parts.

        content: list of {"type": "text", "text": "..."} or
                         {"type": "image", "data": bytes, "mime_type": "image/jpeg"}
        """
        mdl = model or self._default_model
        parts: list = []
        for item in content:
            if item["type"] == "text":
                parts.append(item["text"])
            elif item["type"] == "image":
                parts.append(
                    types.Part.from_bytes(
                        data=item["data"],
                        mime_type=item.get("mime_type", "image/jpeg"),
                    )
                )

        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        response = await self._client.aio.models.generate_content(
            model=mdl,
            contents=parts,
            config=config,
        )
        text = response.text or ""
        usage = response.usage_metadata or {}
        in_tok  = getattr(usage, "prompt_token_count",     0) or 0
        out_tok = getattr(usage, "candidates_token_count", 0) or 0
        return LLMResponse(
            text=text,
            input_tokens=in_tok,
            output_tokens=out_tok,
            model=mdl,
            cost=self.estimate_cost(in_tok, out_tok),
        )

    # ------------------------------------------------------------------
    # Vision (all images at once — kept for API compatibility)
    # ------------------------------------------------------------------

    async def generate_with_vision(
        self,
        system: str,
        messages: list[dict],
        images: list[bytes],
    ) -> LLMResponse:
        content: list[dict] = []
        for img in images:
            content.append({"type": "image", "data": img, "mime_type": "image/jpeg"})
        user_text = "\n\n".join(m["content"] for m in messages if m.get("role") == "user")
        content.append({"type": "text", "text": user_text})
        return await self.generate_with_interleaved_content(system, content)

    # ------------------------------------------------------------------
    # Streaming (not used in the pipeline — raises to fail fast)
    # ------------------------------------------------------------------

    async def stream(self, system: str, messages: list[dict]) -> AsyncIterator[str]:
        raise NotImplementedError("Streaming not implemented for GeminiProvider")

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return input_tokens * _COST_INPUT_PER_M + output_tokens * _COST_OUTPUT_PER_M
