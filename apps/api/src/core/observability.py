import time
from collections.abc import Callable
from functools import wraps
from typing import Any


def track_llm_call(prompt_key: str | None = None) -> Callable:
    """Decorator that records an LLM call to the llm_calls table after it completes."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.monotonic()
            response = await func(*args, **kwargs)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            # TODO: persist to llm_calls table once LLMCall model and db session are wired
            _ = prompt_key, elapsed_ms
            return response
        return wrapper
    return decorator
