"""Provider selection. ``get_provider()`` returns the configured provider or raises LLMError."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from backend.ai.keys import get_key
from backend.ai.types import ChatEvent, ChatRequest, LLMError, LLMRequest, LLMResult
from backend.core.config import get_settings
from backend.core.state import load_state


class Provider(Protocol):
    name: str
    max_parallel: int

    def model_for(self, task: str, tier: str) -> str: ...
    def complete(self, req: LLMRequest) -> LLMResult: ...
    def chat_stream(self, req: ChatRequest) -> Iterator[ChatEvent]: ...


def active_provider_name() -> str:
    # The Settings page can switch provider at runtime (e.g. to Ollama when offline).
    return load_state().get("llm_provider") or get_settings().llm.provider


def is_configured() -> bool:
    name = active_provider_name()
    return name != "anthropic" or bool(get_key("anthropic"))


def get_provider(name: str | None = None) -> Provider:
    cfg = get_settings().llm
    name = name or active_provider_name()
    if name == "anthropic":
        from backend.ai.providers.anthropic_provider import AnthropicProvider

        key = get_key("anthropic")
        if not key:
            raise LLMError("No Anthropic API key set. Add it on the Settings page.")
        return AnthropicProvider(cfg, key, batch=load_state().get("llm_batch"))
    if name == "ollama":
        from backend.ai.providers.local_providers import OllamaProvider

        return OllamaProvider(cfg)
    if name == "openai":
        from backend.ai.providers.local_providers import OpenAICompatProvider

        return OpenAICompatProvider(cfg, get_key("openai"))
    if name == "mock":
        from backend.ai.providers.mock_provider import MockProvider

        return MockProvider()
    raise LLMError(f"Unknown LLM provider {name!r}")
