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


class LLMNoVision(LLMError):
    """The provider can't read images (local text-only models)."""


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
    # Related calls that should share one cached prefix: every call sends all of these output
    # schemas (as tools, for Claude) and answers with the one for ``task``. A per-call output
    # schema would sit in the cached prefix and give every task its own cache entry.
    shared_outputs: dict[str, type[BaseModel]] | None = None
    # Thinking effort for this call instead of the tier's default (Claude only), e.g. "low"
    # for quick conversational turns.
    effort: str | None = None
    # Images to read (handwritten notes): (media type, bytes), sent after the cached context.
    images: list[tuple[str, bytes]] = field(default_factory=list)


@dataclass
class LLMResult:
    parsed: Any
    model: str
    provider: str
    usage: Usage = field(default_factory=Usage)
    attempts: int = 1


@dataclass
class ChatRequest:
    """A free-text, streamed reply (chat).

    ``system`` is stable instructions; ``context`` (optional) is large material placed after it
    with a cache breakpoint, e.g. a whole lecture's transcript for lecture-scoped chat.
    """

    system: str
    messages: list[dict[str, str]]  # [{"role": "user"|"assistant", "content": "..."}]
    context: str = ""
    tier: str = "main"
    max_tokens: int = 16_000


@dataclass
class ChatEvent:
    type: str  # "text" (a delta) | "done"
    text: str = ""
    model: str = ""
    usage: Usage | None = None
