"""LangChain Responses model with admission control and durable spend accounting."""

from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI
from openai.lib._parsing._responses import type_to_text_format_param
from pydantic import Field

from src.tracing.agent_tracer import add_token_usage_from_response
from src.utils.llm_budget import MODEL, LLMBudgetAccountingError, get_llm_budget
from src.utils.llm_queue import (
    LLMTaskPriority,
    arun_with_llm_slot,
    current_llm_priority,
    run_with_llm_slot,
)


class LLMInputLimitError(ValueError):
    pass


class LLMResponseError(RuntimeError):
    pass


class LunaChatOpenAI(ChatOpenAI):
    hqa_role: str = Field(exclude=True)
    hqa_input_limit: int = Field(gt=0, exclude=True)
    hqa_output_limit: int = Field(gt=0, exclude=True)

    def _count_payload(self, messages: list, stop: list[str] | None, kwargs: dict) -> dict:
        for name in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
            if name in kwargs and (not isinstance(kwargs[name], int) or not 0 < kwargs[name] <= self.hqa_output_limit):
                raise ValueError("Output budget override exceeds the role limit")
        payload = self._get_request_payload(messages, stop=stop, **kwargs)
        if payload.get("model") != MODEL or not self.use_responses_api:
            raise ValueError("HQA active agents require the pinned Luna Responses model")
        output = payload.get("max_output_tokens")
        if not isinstance(output, int) or not 0 < output <= self.hqa_output_limit:
            raise ValueError("Output budget must fit the role limit, including reasoning")
        if payload.get("stream") or payload.get("background"):
            raise ValueError("HQA analysis requires a completed non-streaming response")
        if payload.get("service_tier") != "default" or payload.get("extra_body"):
            raise ValueError("HQA accounting requires standard pricing without payload overrides")
        if payload.get("conversation") or payload.get("previous_response_id"):
            raise ValueError("HQA analysis requires explicit input snapshots")
        if payload.get("prompt_cache_retention") or payload.get("prompt_cache_options"):
            raise ValueError("Custom cache retention requires an explicit pricing review")
        if any(tool.get("type") != "function" for tool in payload.get("tools", [])):
            raise ValueError("Paid provider-hosted tools are outside the HQA token budget")
        allowed = {"model", "input", "instructions", "tools", "tool_choice", "parallel_tool_calls", "reasoning", "text", "truncation"}
        counted = {key: value for key, value in payload.items() if key in allowed}
        if "text_format" in payload:
            # Use the SDK's own strict-schema conversion, identical to responses.parse.
            counted["text"] = {**counted.get("text", {}), "format": type_to_text_format_param(payload["text_format"])}
        return counted

    def _check_input(self, counted: Any) -> int:
        tokens = counted.input_tokens
        if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
            raise LLMBudgetAccountingError("Input token counter returned invalid usage")
        if tokens > self.hqa_input_limit:
            raise LLMInputLimitError(f"{self.hqa_role} input has {tokens} tokens; limit is {self.hqa_input_limit}")
        return tokens

    def _begin(self, input_tokens: int) -> str:
        budget = get_llm_budget()
        request_id = budget.reserve(
            self.hqa_role, input_tokens, self.hqa_output_limit,
            critical=current_llm_priority() == LLMTaskPriority.RUNTIME,
        )
        budget.mark_sent(request_id)
        return request_id

    def _finish(self, request_id: str, result: Any) -> Any:
        message = result.generations[0].message
        usage = message.usage_metadata
        if not usage or "input_tokens" not in usage or "output_tokens" not in usage:
            get_llm_budget().mark_unknown(request_id)
            raise LLMBudgetAccountingError("Completed Luna response is missing usage; reservation retained")
        inputs = usage.get("input_token_details", {})
        outputs = usage.get("output_token_details", {})
        get_llm_budget().settle(
            request_id,
            input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
            cached_tokens=inputs.get("cache_read", 0),
            cache_write_tokens=inputs.get("cache_creation", 0),
            reasoning_tokens=outputs.get("reasoning", 0),
        )
        add_token_usage_from_response(message)
        message.response_metadata["hqa_request_id"] = request_id
        status = message.response_metadata.get("status")
        if status and status != "completed":
            raise LLMResponseError(f"Luna response did not complete: {status}")
        blocks = message.content if isinstance(message.content, list) else []
        if message.additional_kwargs.get("refusal") or any(
            isinstance(block, dict) and block.get("type") == "refusal" for block in blocks
        ):
            raise LLMResponseError("Luna refused the analysis request")
        return result

    def _record_error(self, request_id: str, error: BaseException) -> None:
        # A parsed API response can carry usage even when schema parsing fails.
        response = getattr(error, "response", None)
        data = None
        if response is not None:
            try:
                data = response.json()
            except (ValueError, RuntimeError):
                data = None
        usage = data.get("usage") if isinstance(data, dict) else None
        if isinstance(usage, dict) and "input_tokens" in usage and "output_tokens" in usage:
            inputs = usage.get("input_tokens_details") or {}
            outputs = usage.get("output_tokens_details") or {}
            get_llm_budget().settle(
                request_id, input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"],
                cached_tokens=inputs.get("cached_tokens", 0), cache_write_tokens=inputs.get("cache_write_tokens", 0),
                reasoning_tokens=outputs.get("reasoning_tokens", 0),
            )
        elif getattr(error, "status_code", None) in (400, 401, 403, 404, 422, 429):
            get_llm_budget().settle(request_id, input_tokens=0, output_tokens=0)
        else:
            get_llm_budget().mark_unknown(request_id)

    def _generate(self, messages: list, stop=None, run_manager=None, **kwargs):
        counted = run_with_llm_slot(lambda: self.root_client.responses.input_tokens.count(
            **self._count_payload(messages, stop, kwargs)
        ))
        input_tokens = self._check_input(counted)

        def generate():
            request_id = self._begin(input_tokens)
            try:
                result = super(LunaChatOpenAI, self)._generate(messages, stop, run_manager, **kwargs)
            except BaseException as error:
                self._record_error(request_id, error)
                raise
            return self._finish(request_id, result)

        return run_with_llm_slot(generate, tokens=input_tokens + self.hqa_output_limit)

    async def _agenerate(self, messages: list, stop=None, run_manager=None, **kwargs):
        counted = await arun_with_llm_slot(lambda: self.root_async_client.responses.input_tokens.count(
            **self._count_payload(messages, stop, kwargs)
        ))
        input_tokens = self._check_input(counted)

        async def generate():
            request_id = self._begin(input_tokens)
            try:
                result = await super(LunaChatOpenAI, self)._agenerate(messages, stop, run_manager, **kwargs)
            except BaseException as error:
                self._record_error(request_id, error)
                raise
            return self._finish(request_id, result)

        return await arun_with_llm_slot(generate, tokens=input_tokens + self.hqa_output_limit)
