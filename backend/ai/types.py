"""Provider-neutral request/response types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

# Prices in USD per million tokens: (input, output, cache write 5 min, cache read).
# Source: Anthropic pricing as of 2026-10 (claude-api skill). Local providers are free.
PRICING: dict[str, tuple[float, float, float, float]] = {
    "claude-sonnet-5-5": (2.00, 10.00, 2.50, 0.20),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
    "claude-sonnet-5": (2.00, 10.00, 2.50, 0.20),
    "claude-fable-5-1": (10.00, 50.00, 12.50, 0.25),
}


class LLMError(Exception):
    """User-facing LLM failure (bad key, refusal, invalid output after retries...)."""


class LLMRefusal(LLMError):
    pass


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def cost(self, model: str) -> float:
        p = PRICING.get(model)
        if p is None:
            return 0.0
        return (
            self.input_tokens * p[0]
            + self.output_tokens * p[1]
            + self.cache_write_tokens * p[2]
            + self.cache_read_tokens * p[3]
        ) / 1e6

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass
class LLMRequest:
    """One structured-output call.

    ``system`` and ``context`` form the cacheable prefix (identical across the calls for one
    lecture); ``task`` is the per-call instruction that follows the cache breakpoint.
    """

    task: str  # task name, e.g. "notes"; used for model routing and usage records
    system: str
    context: str
    instruction: str
    output: type[BaseModel]
    tier: str = "main"  # main | cheap
    max_tokens: int = 32_000


@dataclass
class LLMResult:
    parsed: Any
    model: str
    provider: str
    usage: Usage = field(default_factory=Usage)
    attempts: int = 1
