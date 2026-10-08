"""Claude via the official Anthropic SDK: streamed structured outputs with prompt caching."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import anthropic
import pydantic

from backend.ai.types import (
    ChatEvent,
    ChatRequest,
    LLMError,
    LLMRefusal,
    LLMRequest,
    LLMResult,
    Usage,
)
from backend.core.config import LLMConfig

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models with server-side refusal fallback on the Claude API (Haiku has none).
FALLBACK_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1")


def tool_name(task: str) -> str:
    return f"submit_{task}"


def tool_defs(outputs: dict[str, type[pydantic.BaseModel]]) -> list[dict[str, Any]]:
    return [
        {
            "name": tool_name(task),
            "description": f"Submit the {task} for this lecture.",
            "input_schema": model.model_json_schema(),
        }
        for task, model in sorted(outputs.items())
    ]


class AnthropicProvider:
    name = "anthropic"
    max_parallel = 3  # concurrent requests the backend serves well

    def __init__(self, cfg: LLMConfig, api_key: str) -> None:
        self.cfg = cfg
        self.client = anthropic.Anthropic(api_key=api_key, timeout=cfg.timeout_s, max_retries=3)

    def model_for(self, task: str, tier: str) -> str:
        return self.cfg.task_models.get(task) or (
            self.cfg.anthropic_main if tier == "main" else self.cfg.anthropic_cheap
        )

    def complete(self, req: LLMRequest) -> LLMResult:
        model = self.model_for(req.task, req.tier)
        effort = self.cfg.main_effort if req.tier == "main" else self.cfg.cheap_effort
        tool_mode = bool(req.shared_outputs) and req.task in (req.shared_outputs or {})
        instruction = req.instruction
        if tool_mode:
            instruction += (
                f"\n\nSubmit your answer by calling the `{tool_name(req.task)}` tool exactly once."
            )
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": req.max_tokens,
            # Prefix = system + lecture context, identical for every call on this lecture, so
            # the breakpoint after the context lets all calls after the first read the cache.
            "system": [{"type": "text", "text": req.system}],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": req.context,
                            "cache_control": {"type": "ephemeral"},
                        },
                        {"type": "text", "text": instruction},
                    ],
                }
            ],
            "output_config": {"effort": effort},
        }
        if tool_mode:
            # The same tool list (sorted, identical bytes) on every related call keeps the
            # cached prefix shared. Non-strict: strict tools compile into a grammar that is
            # too large for seven schemas; Pydantic validates the arguments instead.
            kwargs["tools"] = tool_defs(req.shared_outputs or {})
        else:
            kwargs["output_format"] = req.output
        if self.cfg.anthropic_fallbacks and model in FALLBACK_MODELS:
            kwargs["fallbacks"] = "default"
            kwargs["betas"] = [FALLBACK_BETA]

        usage = Usage()
        last_error = ""
        for attempt in range(1, self.cfg.max_retries + 2):
            try:
                with self.client.beta.messages.stream(**kwargs) as stream:
                    msg = stream.get_final_message()
            except pydantic.ValidationError as e:
                # Output didn't match the schema (e.g. cut off at max_tokens). Retry.
                last_error = f"invalid output: {e.errors()[0].get('msg', e)}"
                log.warning("%s attempt %d: %s", req.task, attempt, last_error)
                continue
            except anthropic.AuthenticationError as e:
                raise LLMError("The Anthropic API key was rejected. Check it in Settings.") from e
            except anthropic.PermissionDeniedError as e:
                raise LLMError(f"Anthropic API permission denied: {e.message}") from e
            except anthropic.NotFoundError as e:
                raise LLMError(f"Model {model!r} not found. Check the model name in config.") from e
            except anthropic.RateLimitError as e:
                raise LLMError("Anthropic rate limit hit; retry in a minute.") from e
            except anthropic.BadRequestError as e:
                msg_text = getattr(e, "message", str(e))
                if "credit balance" in msg_text.lower():
                    raise LLMError("Your Anthropic credit balance is too low.") from e
                raise LLMError(f"Anthropic rejected the request: {msg_text}") from e
            except anthropic.APIStatusError as e:
                last_error = f"API error {e.status_code}"
                log.warning("%s attempt %d: %s", req.task, attempt, last_error)
                continue
            except anthropic.APIConnectionError as e:
                raise LLMError("Could not reach the Anthropic API (offline?).") from e

            u = msg.usage
            usage = usage + Usage(
                input_tokens=u.input_tokens or 0,
                output_tokens=u.output_tokens or 0,
                cache_read_tokens=u.cache_read_input_tokens or 0,
                cache_write_tokens=u.cache_creation_input_tokens or 0,
            )
            if msg.stop_reason == "refusal":
                category = getattr(msg.stop_details, "category", None) if msg.stop_details else None
                raise LLMRefusal(f"Claude declined this request ({category or 'no category'}).")
            if tool_mode:
                call = next(
                    (
                        b
                        for b in msg.content
                        if b.type == "tool_use" and b.name == tool_name(req.task)
                    ),
                    None,
                )
                try:
                    parsed = req.output.model_validate(call.input) if call else None
                except pydantic.ValidationError as e:
                    last_error = f"invalid tool input: {e.errors()[0].get('msg', e)}"
                    log.warning("%s attempt %d: %s", req.task, attempt, last_error)
                    continue
            else:
                parsed = next(
                    (b.parsed_output for b in msg.content if b.type == "text" and b.parsed_output),
                    None,
                )
            if parsed is None:
                last_error = f"no structured output (stop_reason={msg.stop_reason})"
                log.warning("%s attempt %d: %s", req.task, attempt, last_error)
                continue
            log.info(
                "%s via %s: in=%d out=%d cache_read=%d cache_write=%d",
                req.task,
                msg.model,
                usage.input_tokens,
                usage.output_tokens,
                usage.cache_read_tokens,
                usage.cache_write_tokens,
            )
            return LLMResult(parsed, msg.model, self.name, usage, attempt)
        raise LLMError(
            f"{req.task}: no valid output after {self.cfg.max_retries + 1} attempts ({last_error})"
        )

    def chat_stream(self, req: ChatRequest) -> Iterator[ChatEvent]:
        model = self.model_for("chat", req.tier)
        system: list[dict[str, Any]] = [{"type": "text", "text": req.system}]
        if req.context:
            # Large, stable material (a whole lecture) after the instructions, cached so that
            # follow-up questions in the same chat only pay for the new turn.
            system.append(
                {"type": "text", "text": req.context, "cache_control": {"type": "ephemeral"}}
            )
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": req.max_tokens,
            "system": system,
            "messages": req.messages,
            "output_config": {"effort": self.cfg.chat_effort},
        }
        if self.cfg.anthropic_fallbacks and model in FALLBACK_MODELS:
            kwargs["fallbacks"] = "default"
            kwargs["betas"] = [FALLBACK_BETA]
        try:
            with self.client.beta.messages.stream(**kwargs) as stream:
                for delta in stream.text_stream:
                    yield ChatEvent("text", delta)
                msg = stream.get_final_message()
        except anthropic.AuthenticationError as e:
            raise LLMError("The Anthropic API key was rejected. Check it in Settings.") from e
        except anthropic.RateLimitError as e:
            raise LLMError("Anthropic rate limit hit; try again in a minute.") from e
        except anthropic.APIConnectionError as e:
            raise LLMError("Could not reach the Anthropic API (offline?). Try Ollama.") from e
        except anthropic.APIStatusError as e:
            raise LLMError(f"Anthropic error {e.status_code}: {getattr(e, 'message', e)}") from e
        if msg.stop_reason == "refusal":
            yield ChatEvent("text", "\n\n_Claude declined to answer this._")
        u = msg.usage
        usage = Usage(
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_read_tokens=u.cache_read_input_tokens or 0,
            cache_write_tokens=u.cache_creation_input_tokens or 0,
        )
        yield ChatEvent("done", model=msg.model, usage=usage)
