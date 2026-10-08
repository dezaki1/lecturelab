"""LLM settings: provider switch, API key storage (never returned), connection test."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.ai import keys
from backend.ai.providers import active_provider_name, get_provider
from backend.ai.types import LLMError, LLMRequest
from backend.core.config import get_settings
from backend.core.events import bus
from backend.core.state import update_state

router = APIRouter(prefix="/api/settings", tags=["settings"])

PROVIDERS = ("anthropic", "ollama", "openai", "mock")


class KeyRequest(BaseModel):
    provider: str = "anthropic"
    key: str = Field(min_length=10, max_length=500)


class ProviderRequest(BaseModel):
    provider: str


class Ping(BaseModel):
    ok: bool


@router.get("/llm")
def llm_settings() -> dict[str, Any]:
    cfg = get_settings().llm
    return {
        "provider": active_provider_name(),
        "providers": list(PROVIDERS),
        "anthropic": {
            "main": cfg.anthropic_main,
            "cheap": cfg.anthropic_cheap,
            "main_effort": cfg.main_effort,
            "key": keys.key_hint("anthropic"),
        },
        "ollama": {"url": cfg.ollama_url, "model": cfg.ollama_model},
        "openai": {"base_url": cfg.openai_base_url, "model": cfg.openai_model},
        "generation": get_settings().generation.model_dump(),
    }


@router.put("/llm/provider")
def set_provider(req: ProviderRequest) -> dict[str, Any]:
    if req.provider not in PROVIDERS:
        raise HTTPException(422, f"provider must be one of {PROVIDERS}")
    update_state(llm_provider=req.provider)
    bus.publish("settings", {})
    return llm_settings()


@router.put("/api-key")
def put_key(req: KeyRequest) -> dict[str, Any]:
    if req.provider not in ("anthropic", "openai"):
        raise HTTPException(422, "Unknown provider")
    key = req.key.strip()
    if req.provider == "anthropic" and not key.startswith("sk-ant-"):
        raise HTTPException(422, "That doesn't look like an Anthropic API key (sk-ant-…)")
    try:
        keys.set_key(req.provider, key)
    except Exception as e:
        raise HTTPException(500, f"Could not store the key: {type(e).__name__}") from e
    bus.publish("settings", {})
    return keys.key_hint(req.provider)


@router.delete("/api-key/{provider}")
def delete_key(provider: str) -> dict[str, Any]:
    keys.delete_key(provider)
    bus.publish("settings", {})
    return keys.key_hint(provider)


@router.post("/llm/test")
def test_llm() -> dict[str, Any]:
    """A minimal real call with the cheap model (costs a fraction of a cent)."""
    try:
        provider = get_provider()
        result = provider.complete(
            LLMRequest(
                task="test",
                system="Reply with ok=true.",
                context="Connection test.",
                instruction="Return ok=true.",
                output=Ping,
                tier="cheap",
                max_tokens=2000,
            )
        )
    except LLMError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": bool(result.parsed.ok), "model": result.model, "provider": result.provider}
