"""Ollama (local/offline) and generic OpenAI-compatible endpoints, via plain HTTP.

Both get the JSON schema of the expected output and are validated with Pydantic like Claude.
Local models are slower and weaker; they don't support prompt caching, so the full context is
sent on every call.
"""

from __future__ import annotations

import json
import logging

import httpx2 as httpx
import pydantic

from backend.ai.types import LLMError, LLMRequest, LLMResult, Usage
from backend.core.config import LLMConfig

log = logging.getLogger(__name__)


def _validate(req: LLMRequest, text: str) -> object:
    return req.output.model_validate_json(text)


class OllamaProvider:
    name = "ollama"

    def __init__(self, cfg: LLMConfig) -> None:
        self.cfg = cfg

    def model_for(self, task: str, tier: str) -> str:
        return self.cfg.task_models.get(task) or self.cfg.ollama_model

    def complete(self, req: LLMRequest) -> LLMResult:
        model = self.model_for(req.task, req.tier)
        body = {
            "model": model,
            "stream": False,
            "format": req.output.model_json_schema(),
            "options": {"num_ctx": self.cfg.ollama_num_ctx, "temperature": 0.2},
            "messages": [
                {"role": "system", "content": req.system},
                {"role": "user", "content": f"{req.context}\n\n{req.instruction}"},
            ],
        }
        usage = Usage()
        last = ""
        for attempt in range(1, self.cfg.max_retries + 2):
            try:
                r = httpx.post(
                    f"{self.cfg.ollama_url.rstrip('/')}/api/chat",
                    json=body,
                    timeout=self.cfg.timeout_s,
                )
            except httpx.HTTPError as e:
                raise LLMError(f"Ollama is not reachable at {self.cfg.ollama_url} ({e})") from e
            if r.status_code == 404:
                raise LLMError(f"Ollama model {model!r} is not installed (ollama pull {model})")
            if r.status_code >= 400:
                raise LLMError(f"Ollama error {r.status_code}: {r.text[:300]}")
            data = r.json()
            usage = usage + Usage(
                input_tokens=data.get("prompt_eval_count", 0),
                output_tokens=data.get("eval_count", 0),
            )
            try:
                parsed = _validate(req, data["message"]["content"])
            except (pydantic.ValidationError, KeyError, ValueError) as e:
                last = str(e)[:200]
                log.warning("ollama %s attempt %d invalid: %s", req.task, attempt, last)
                continue
            return LLMResult(parsed, model, self.name, usage, attempt)
        raise LLMError(f"{req.task}: Ollama gave no valid output ({last})")


class OpenAICompatProvider:
    name = "openai"

    def __init__(self, cfg: LLMConfig, api_key: str | None) -> None:
        if not cfg.openai_base_url or not cfg.openai_model:
            raise LLMError("Set llm.openai_base_url and llm.openai_model in config.toml")
        self.cfg = cfg
        self.api_key = api_key

    def model_for(self, task: str, tier: str) -> str:
        return self.cfg.task_models.get(task) or self.cfg.openai_model

    def complete(self, req: LLMRequest) -> LLMResult:
        model = self.model_for(req.task, req.tier)
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": req.system},
                {"role": "user", "content": f"{req.context}\n\n{req.instruction}"},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": req.output.__name__,
                    "schema": req.output.model_json_schema(),
                },
            },
        }
        usage = Usage()
        last = ""
        for attempt in range(1, self.cfg.max_retries + 2):
            try:
                r = httpx.post(
                    f"{self.cfg.openai_base_url.rstrip('/')}/chat/completions",
                    json=body,
                    headers=headers,
                    timeout=self.cfg.timeout_s,
                )
            except httpx.HTTPError as e:
                raise LLMError(f"Endpoint not reachable: {e}") from e
            if r.status_code >= 400:
                raise LLMError(f"Endpoint error {r.status_code}: {r.text[:300]}")
            data = r.json()
            u = data.get("usage") or {}
            usage = usage + Usage(
                input_tokens=u.get("prompt_tokens", 0), output_tokens=u.get("completion_tokens", 0)
            )
            try:
                parsed = _validate(req, data["choices"][0]["message"]["content"])
            except (
                pydantic.ValidationError,
                KeyError,
                IndexError,
                ValueError,
                json.JSONDecodeError,
            ) as e:
                last = str(e)[:200]
                continue
            return LLMResult(parsed, model, self.name, usage, attempt)
        raise LLMError(f"{req.task}: no valid output ({last})")
