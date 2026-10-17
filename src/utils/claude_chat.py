"""LangChain chat model for Claude with the same admission control and spend accounting as Luna."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

import anthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel, Field, PrivateAttr, SecretStr, model_validator

from src.tracing.agent_tracer import add_token_usage_from_response
from src.utils.llm_budget import (
    PRICES,
    Attempt,
    LLMBudgetAccountingError,
    LLMBudgetExceeded,
    get_llm_budget,
    model_price,
)
from src.utils.llm_errors import LLMInputLimitError, LLMResponseError
from src.utils.llm_queue import LLMTaskPriority, current_llm_priority, run_with_llm_slot

logger = logging.getLogger(__name__)

API_BASE_URL = "https://api.anthropic.com"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
MAX_OUTPUT_TOKENS = 128_000
# With `fallbacks: "default"` the API re-runs a request a safety classifier declined on the model
# Anthropic recommends for that category (Claude Opus 5 or Opus 4.8) inside the same call. Claude
# Haiku 5.5 has no server-side fallback. A fallback reservation adds one attempt at Opus 5 rates,
# the dearer of the two targets.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = frozenset({"claude-opus-5-5", "claude-sonnet-5-5"})
FALLBACK_RESERVATION_MODEL = "claude-opus-5"
# Rejected before any generation, so nothing is billed.
_UNBILLED_STATUSES = frozenset({400, 401, 403, 404, 413, 422, 429, 529})


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                parts.append(part["text"])
            else:
                raise ValueError("HQA Claude requests carry text content only")
        return "".join(parts)
    raise ValueError("HQA Claude requests carry text content only")


def _attempt(model: str, usage: Any) -> Attempt:
    read = usage.cache_read_input_tokens or 0
    write = usage.cache_creation_input_tokens or 0
    return Attempt(model, usage.input_tokens + read + write, usage.output_tokens, read, write)


class ClaudeRoleModel(BaseChatModel):
    """What every HQA Claude role model shares: the model, effort and role limits, the request
    checks, and JSON output validated against the full schema. Thinking is adaptive (Claude Opus
    5.5 cannot turn it off), so `effort` is the depth control and the output limit covers thinking
    plus the answer."""

    model: str
    effort: str
    request_timeout: float = Field(gt=0)
    hqa_role: str = Field(exclude=True)
    hqa_input_limit: int = Field(gt=0, exclude=True)
    hqa_output_limit: int = Field(gt=0, exclude=True)
    # Every call must be accounted for, so a LangChain cache may never answer one.
    cache: bool | None = Field(default=False, exclude=True)

    @model_validator(mode="after")
    def _check_role_model(self) -> "ClaudeRoleModel":
        if not self.model.startswith("claude-") or self.model not in PRICES:
            raise ValueError(f"{self.model} is not a supported Claude model")
        if self.effort not in EFFORTS:
            raise ValueError(f"Claude effort must be one of {', '.join(EFFORTS)}")
        if self.hqa_output_limit > MAX_OUTPUT_TOKENS:
            raise ValueError(f"Claude output is limited to {MAX_OUTPUT_TOKENS} tokens")
        if self.cache is not False:
            raise ValueError("HQA Claude calls must bypass LangChain caches")
        return self

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model": self.model, "effort": self.effort, "role": self.hqa_role}

    # Read by the shared analysis for its result-cache keys and audit records.
    @property
    def model_name(self) -> str:
        return self.model

    @property
    def reasoning_effort(self) -> str:
        return self.effort

    @property
    def max_tokens(self) -> int:
        return self.hqa_output_limit

    def with_structured_output(self, schema: dict | type, *, include_raw: bool = False, method: str = "json_schema",
                               strict: bool | None = None, **kwargs: Any) -> Runnable:
        """JSON output constrained to `schema`; the full schema, including what Claude does not
        enforce itself (lengths, ranges, patterns, cross-field validators), is validated here."""
        if kwargs:
            raise ValueError(f"Unsupported structured-output options: {sorted(kwargs)}")
        if method not in ("json_schema", "json_mode", "function_calling"):
            raise ValueError(f"Unsupported structured-output method: {method}")
        is_model = isinstance(schema, type) and issubclass(schema, BaseModel)
        if not is_model and not isinstance(schema, dict):
            raise ValueError("Structured output needs a pydantic model or a JSON schema dict")

        def parse(message: AIMessage) -> Any:
            return schema.model_validate_json(message.content) if is_model else json.loads(message.content)

        def parse_with_raw(message: AIMessage) -> dict:
            try:
                return {"raw": message, "parsed": parse(message), "parsing_error": None}
            except ValueError as error:
                return {"raw": message, "parsed": None, "parsing_error": error}

        return self.bind(hqa_output_schema=schema) | RunnableLambda(parse_with_raw if include_raw else parse)

    def _conversation(self, messages: list[BaseMessage], kwargs: dict) -> tuple[int, str, list[dict], Any]:
        """The output limit, system text, user/assistant turns and bound output schema of a call."""
        kwargs = {key: value for key, value in kwargs.items() if key != "ls_structured_output_format"}
        unknown = set(kwargs) - {"max_tokens", "hqa_output_schema"}
        if unknown:
            raise ValueError(f"Unsupported Claude request overrides: {sorted(unknown)}")
        max_tokens = kwargs.get("max_tokens", self.hqa_output_limit)
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 0 < max_tokens <= self.hqa_output_limit:
            raise ValueError("Output budget override exceeds the role limit")
        system, turns = [], []
        for message in messages:
            text = _text(message.content)
            if message.type == "system":
                if turns:
                    raise ValueError("System instructions must come before the conversation")
                system.append(text)
            elif message.type in ("human", "ai"):
                if not text.strip():
                    raise ValueError("Claude messages must not be empty")
                turns.append({"role": "user" if message.type == "human" else "assistant", "content": text})
            else:
                raise ValueError(f"Unsupported message type for Claude: {message.type}")
        if not turns or turns[0]["role"] != "user" or turns[-1]["role"] != "user":
            # Claude Opus 5.5 rejects an assistant prefill; a request must start and end with the user.
            raise ValueError("Claude requests must start and end with a user message")
        return max_tokens, "\n\n".join(system), turns, kwargs.get("hqa_output_schema")


class ClaudeChat(ClaudeRoleModel):
    """Claude Messages API model for one HQA role, paid from API credits.

    Each call counts its input with the token-counting endpoint and refuses input above the role
    limit, reserves the worst-case spend in the shared ledger, sends one non-streaming request
    without SDK retries, and settles the ledger from the reported usage before the answer is used.
    """

    api_key: SecretStr = Field(exclude=True, repr=False)
    fallbacks: bool = True

    _client: Any = PrivateAttr(default=None)
    _client_lock: Any = PrivateAttr(default_factory=threading.Lock)

    @model_validator(mode="after")
    def _check_fallbacks(self) -> "ClaudeChat":
        if self.fallbacks and self.model not in FALLBACK_MODELS:
            raise ValueError(f"{self.model} has no server-side fallback")
        return self

    @property
    def _llm_type(self) -> str:
        return "hqa-claude"

    @property
    def client(self) -> anthropic.Anthropic:
        with self._client_lock:
            if self._client is None:
                # Explicit key and base URL: the SDK then ignores ANTHROPIC_AUTH_TOKEN, profiles and
                # ANTHROPIC_BASE_URL from the environment, so nothing else can redirect or add credentials.
                self._client = anthropic.Anthropic(api_key=self.api_key.get_secret_value(), base_url=API_BASE_URL,
                                                   max_retries=0, timeout=self.request_timeout)
            return self._client

    def _request(self, messages: list[BaseMessage], stop: list[str] | None, kwargs: dict) -> dict:
        max_tokens, system, turns, schema = self._conversation(messages, kwargs)
        request: dict[str, Any] = {"model": self.model, "max_tokens": max_tokens, "messages": turns,
                                   "output_config": {"effort": self.effort}}
        if system:
            request["system"] = system
        if stop:
            request["stop_sequences"] = list(stop)
        if schema is not None:
            # The SDK moves constraints the API does not enforce (lengths, ranges, patterns) into
            # field descriptions; the full schema is validated after the response is settled.
            request["output_config"]["format"] = {"type": "json_schema", "schema": anthropic.transform_schema(schema)}
        return request

    def _count(self, request: dict) -> int:
        params = {key: request[key] for key in ("model", "messages", "system") if key in request}
        if "format" in request["output_config"]:
            params["output_config"] = {"format": request["output_config"]["format"]}
        counted = run_with_llm_slot(lambda: self.client.messages.count_tokens(**params, timeout=self.request_timeout))
        tokens = counted.input_tokens
        if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
            raise LLMBudgetAccountingError("Input token counter returned invalid usage")
        if tokens > self.hqa_input_limit:
            raise LLMInputLimitError(f"{self.hqa_role} input has {tokens} tokens; limit is {self.hqa_input_limit}")
        return tokens

    def _begin(self, input_tokens: int, max_tokens: int) -> str:
        budget = get_llm_budget()
        request_id = budget.reserve(
            self.hqa_role, input_tokens, max_tokens, critical=current_llm_priority() == LLMTaskPriority.RUNTIME,
            model=self.model, fallback_models=(FALLBACK_RESERVATION_MODEL,) if self.fallbacks else (),
        )
        budget.mark_sent(request_id)
        return request_id

    def _send(self, request: dict) -> tuple[Any, dict[str, str]]:
        if self.fallbacks:
            raw = self.client.beta.messages.with_raw_response.create(
                **request, betas=[FALLBACK_BETA], fallbacks="default", timeout=self.request_timeout)
        else:
            raw = self.client.messages.with_raw_response.create(**request, timeout=self.request_timeout)
        # The organization's current rate limits, for setting HQA_LLM_RPM / HQA_LLM_TPM.
        limits = {name.lower().removeprefix("anthropic-ratelimit-"): value for name, value in raw.headers.items()
                  if name.lower().startswith("anthropic-ratelimit-")}
        return raw.parse(), limits

    def _attempts(self, response: Any) -> tuple[list[Attempt], bool]:
        """Billed attempts; with a server-side fallback `usage.iterations` lists each one, and the
        top-level usage covers only the attempt that produced the returned message."""
        attempts, fallback_served = [], False
        for item in getattr(response.usage, "iterations", None) or []:
            if item.type not in ("message", "fallback_message"):
                raise LLMBudgetAccountingError(f"Unpriced Claude usage iteration {item.type!r}")
            fallback_served = fallback_served or item.type == "fallback_message"
            # A `message` entry may leave out its model; it is then the requested one.
            attempts.append(_attempt(item.model or self.model, item))
        attempts = attempts or [_attempt(self.model, response.usage)]
        for attempt in attempts:
            model_price(attempt.model)  # an unpriced fallback model leaves the request for operator review
        return attempts, fallback_served

    def _record_error(self, request_id: str, error: BaseException) -> None:
        if isinstance(error, anthropic.APIStatusError) and error.status_code in _UNBILLED_STATUSES:
            get_llm_budget().settle(request_id, input_tokens=0, output_tokens=0)
        else:
            # A timeout or a dropped connection may still have been processed and billed.
            get_llm_budget().mark_unknown(request_id)

    def _finish(self, request_id: str, response: Any, rate_limits: dict[str, str], max_tokens: int) -> AIMessage:
        budget = get_llm_budget()
        try:
            attempts, fallback_served = self._attempts(response)
        except LLMBudgetAccountingError:
            budget.mark_unknown(request_id)
            raise
        details = getattr(response.usage, "output_tokens_details", None)
        reasoning = min(getattr(details, "thinking_tokens", 0) or 0, response.usage.output_tokens)
        budget.settle_attempts(request_id, attempts, reasoning_tokens=reasoning)

        inputs = sum(attempt.input_tokens for attempt in attempts)
        outputs = sum(attempt.output_tokens for attempt in attempts)
        text = "".join(block.text for block in response.content if block.type == "text")
        message = AIMessage(
            content=text,
            usage_metadata={
                "input_tokens": inputs, "output_tokens": outputs, "total_tokens": inputs + outputs,
                "input_token_details": {"cache_read": sum(a.cached_tokens for a in attempts),
                                        "cache_creation": sum(a.cache_write_tokens for a in attempts)},
                "output_token_details": {"reasoning": reasoning},
            },
            response_metadata={
                "model_name": self.model, "served_model": response.model, "fallback_served": fallback_served,
                "stop_reason": response.stop_reason, "hqa_request_id": request_id,
                "provider_request_id": getattr(response, "_request_id", None), "rate_limits": rate_limits,
            },
        )
        add_token_usage_from_response(message)
        if fallback_served:
            logger.warning("Claude %s request %s was declined by %s and answered by %s",
                           self.hqa_role, request_id, self.model, response.model)
        if response.stop_reason == "refusal":
            category = getattr(getattr(response, "stop_details", None), "category", None)
            raise LLMResponseError(f"Claude declined the {self.hqa_role} request (category: {category or 'unspecified'})")
        if response.stop_reason == "max_tokens":
            raise LLMResponseError(f"Claude stopped at the {max_tokens}-token output limit; the answer is incomplete")
        if response.stop_reason not in ("end_turn", "stop_sequence"):
            raise LLMResponseError(f"Claude response ended with {response.stop_reason}")
        if not text.strip():
            raise LLMResponseError("Claude returned no text")
        return message

    def _call(self, messages: list[BaseMessage], stop: list[str] | None, kwargs: dict) -> ChatResult:
        request = self._request(messages, stop, kwargs)
        input_tokens = self._count(request)

        def generate() -> AIMessage:
            request_id = self._begin(input_tokens, request["max_tokens"])
            try:
                response, rate_limits = self._send(request)
            except BaseException as error:
                self._record_error(request_id, error)
                raise
            return self._finish(request_id, response, rate_limits, request["max_tokens"])

        message = run_with_llm_slot(generate, tokens=input_tokens + request["max_tokens"])
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None,
                  **kwargs: Any) -> ChatResult:
        try:
            return self._call(messages, stop, kwargs)
        except anthropic.BadRequestError as error:
            # The organization's prepaid balance (for example a Max plan's monthly API credits) ran out.
            if "credit balance is too low" in str(error).lower():
                raise LLMBudgetExceeded("Claude API credit balance is exhausted; calls resume when the next "
                                        "monthly credits arrive or credits are added in the Console") from error
            raise
