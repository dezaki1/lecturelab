"""Claude via the official Anthropic SDK: streamed structured outputs with prompt caching."""

from __future__ import annotations

import base64
import logging
import time
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
# Background work (notes, cards, cleanup, overviews) can go through the Message Batches API:
# half price, answers usually within minutes (at most 24 h). Chat and other interactive
# calls never do.
BATCH_TASKS = {
    "hints", "summary", "notes", "glossary", "announcements", "flashcards", "exercises",
    "questions", "explanations", "verify", "cleanup", "subject_hub", "cheatsheet",
}  # fmt: skip
BATCH_POLL_S = (5, 10, 20, 30)  # then every 30 s


class _UseStreaming(Exception):
    """Batch mode can't serve this call (refused, too slow): send it the normal way."""


# Models with server-side refusal fallback on the Claude API (Haiku has none).
FALLBACK_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1")


SUBMIT = "\n\nSubmit your answer by calling the `{tool}` tool exactly once."


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

    def __init__(self, cfg: LLMConfig, api_key: str, batch: bool | None = None) -> None:
        self.cfg = cfg
        self.client = anthropic.Anthropic(api_key=api_key, timeout=cfg.timeout_s, max_retries=3)
        self.batch = cfg.anthropic_batch if batch is None else batch
        # Concurrent requests: 3 streams serve well; batched calls just wait, so send them all.
        self.max_parallel = 8 if self.batch else 3

    def model_for(self, task: str, tier: str) -> str:
        return self.cfg.task_models.get(task) or (
            self.cfg.anthropic_main if tier == "main" else self.cfg.anthropic_cheap
        )

    def complete(self, req: LLMRequest) -> LLMResult:
        model = self.model_for(req.task, req.tier)
        effort = req.effort or (
            self.cfg.main_effort if req.tier == "main" else self.cfg.cheap_effort
        )
        tool_mode = bool(req.shared_outputs) and req.task in (req.shared_outputs or {})
        instruction = req.instruction
        if tool_mode:
            instruction += SUBMIT.format(tool=tool_name(req.task))
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
                        *(
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media,
                                    "data": base64.b64encode(data).decode(),
                                },
                            }
                            for media, data in req.images
                        ),
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
        if self.batch and req.task in BATCH_TASKS:
            try:
                return self._complete_batch(req, kwargs, tool_mode)
            except _UseStreaming as e:
                log.info("%s: %s; sending it the normal way", req.task, e)
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

    def _complete_batch(
        self, req: LLMRequest, kwargs: dict[str, Any], tool_mode: bool
    ) -> LLMResult:
        """The same request as a one-request Message Batch (half price), waiting for it."""
        params = {k: v for k, v in kwargs.items() if k != "output_format"}
        # The calls of one lecture share the cached lecture text; a batch can take longer
        # than the 5-minute cache, so keep it an hour.
        first = params["messages"][0]
        content = [
            {**block, "cache_control": {"type": "ephemeral", "ttl": "1h"}}
            if "cache_control" in block
            else block
            for block in first["content"]
        ]
        if not tool_mode:  # a single output schema becomes a single tool (no SDK parsing here)
            params["tools"] = [
                {
                    "name": tool_name(req.task),
                    "description": f"Submit the {req.task}.",
                    "input_schema": req.output.model_json_schema(),
                }
            ]
            content[-1] = {
                **content[-1],
                "text": content[-1]["text"] + SUBMIT.format(tool=tool_name(req.task)),
            }
        params["messages"] = [{**first, "content": content}]
        usage = Usage(batch=True)
        last_error = ""
        for attempt in range(1, self.cfg.max_retries + 2):
            msg = self._run_batch(req.task, params)
            u = msg.usage
            usage = usage + Usage(
                input_tokens=u.input_tokens or 0,
                output_tokens=u.output_tokens or 0,
                cache_read_tokens=u.cache_read_input_tokens or 0,
                cache_write_1h_tokens=u.cache_creation_input_tokens or 0,
                batch=True,
            )
            if msg.stop_reason == "refusal":  # no server-side fallback in batches
                raise _UseStreaming("declined in the batch")
            call = next(
                (b for b in msg.content if b.type == "tool_use" and b.name == tool_name(req.task)),
                None,
            )
            try:
                parsed = req.output.model_validate(call.input) if call else None
            except pydantic.ValidationError as e:
                last_error = f"invalid tool input: {e.errors()[0].get('msg', e)}"
                log.warning("%s batch attempt %d: %s", req.task, attempt, last_error)
                continue
            if parsed is None:
                last_error = f"no structured output (stop_reason={msg.stop_reason})"
                log.warning("%s batch attempt %d: %s", req.task, attempt, last_error)
                continue
            log.info(
                "%s via %s (batch): in=%d out=%d cache_read=%d cache_write=%d",
                req.task,
                msg.model,
                usage.input_tokens,
                usage.output_tokens,
                usage.cache_read_tokens,
                usage.cache_write_1h_tokens,
            )
            return LLMResult(parsed, msg.model, self.name, usage, attempt)
        raise LLMError(
            f"{req.task}: no valid output after {self.cfg.max_retries + 1} attempts ({last_error})"
        )

    def _run_batch(self, task: str, params: dict[str, Any]) -> Any:
        from backend.core import jobs

        try:
            batch = self.client.messages.batches.create(
                requests=[{"custom_id": task, "params": params}]
            )
        except anthropic.APIConnectionError as e:
            raise LLMError("Could not reach the Anthropic API (offline?).") from e
        except anthropic.APIStatusError as e:
            raise _UseStreaming(f"batch not accepted ({e.status_code})") from e
        started = time.monotonic()
        polls = 0
        while batch.processing_status != "ended":
            if jobs.shutting_down():  # don't leave it running (and billed) behind us
                self._cancel(batch.id)
                raise LLMError("Stopped for shutdown")
            if time.monotonic() - started > self.cfg.batch_timeout_s:
                self._cancel(batch.id)
                raise _UseStreaming(f"batch still running after {self.cfg.batch_timeout_s:.0f} s")
            time.sleep(BATCH_POLL_S[min(polls, len(BATCH_POLL_S) - 1)])
            polls += 1
            try:
                batch = self.client.messages.batches.retrieve(batch.id)
            except (anthropic.APIConnectionError, anthropic.APIStatusError) as e:
                log.warning("%s: checking batch %s failed (%s); retrying", task, batch.id, e)
        for item in self.client.messages.batches.results(batch.id):
            if item.result.type == "succeeded":
                return item.result.message
            if item.result.type == "errored" and item.result.error.type == "invalid_request":
                raise LLMError(f"Anthropic rejected the request: {item.result.error}")
            raise _UseStreaming(f"batch request {item.result.type}")
        raise _UseStreaming("batch returned no result")

    def _cancel(self, batch_id: str) -> None:
        try:
            self.client.messages.batches.cancel(batch_id)
        except anthropic.APIError:
            log.warning("Could not cancel batch %s", batch_id)

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
