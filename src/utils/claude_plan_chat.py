"""Claude role model that runs the logged-in Claude Code CLI headless, so analysis calls draw on
the user's Claude subscription limits instead of API credits.

Anthropic allows the Agent SDK and `claude -p` on a subscription for the subscriber's own use;
offering claude.ai login or subscription limits to other people in a product needs Anthropic's
approval. Each call starts the CLI with every customization off (`--safe-mode`: no CLAUDE.md,
skills, plugins, hooks or MCP servers), no tools, no settings files and no saved session, in an
empty working directory, with an environment that carries no API key, so a call can neither bill
API credits nor see the project or the user's Claude Code setup.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
from functools import lru_cache
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, PrivateAttr, SecretStr, model_validator

from src.tracing.agent_tracer import add_token_usage_from_response
from src.utils.claude_chat import ClaudeRoleModel
from src.utils.llm_budget import LLMBudgetExceeded
from src.utils.llm_errors import LLMResponseError
from src.utils.llm_queue import run_with_llm_slot

logger = logging.getLogger(__name__)

# Before 2.1.205 the CLI silently dropped a JSON schema that used "format" (the RiskManager's
# date-time fields) and answered in free text.
MIN_CLI_VERSION = (2, 1, 205)
# Replaces Claude Code's own coding-agent system prompt when a caller sends none.
DEFAULT_SYSTEM_PROMPT = "Answer the request directly."
_ENV_PASSTHROUGH = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TZ",
                    "CLAUDE_CONFIG_DIR", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy")
_LIMIT_PATTERN = re.compile(r"usage limit|hit your limit|limit reached|limit will reset|out of extra usage", re.I)
# The CLI process starts and exits around the model call.
_STARTUP_SECONDS = 15.0


def _cli_env(token: str | None = None, max_tokens: int | None = None) -> dict[str, str]:
    """A minimal environment: no ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or ANTHROPIC_BASE_URL, so
    the CLI uses the subscription login, and no HQA or broker secrets."""
    env = {name: os.environ[name] for name in _ENV_PASSTHROUGH if os.environ.get(name)}
    env.update({"CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1"})
    if token:
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    if max_tokens:
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(max_tokens)
    return env


@lru_cache(maxsize=8)
def cli_version(cli_path: str) -> tuple[int, int, int]:
    output = subprocess.run([cli_path, "--version"], capture_output=True, text=True, timeout=60, env=_cli_env()).stdout
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", output)
    if not match:
        raise ValueError(f"Cannot read the Claude CLI version from {cli_path}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


class ClaudePlanChat(ClaudeRoleModel):
    """Claude role model on the user's Claude subscription, through `claude -p`.

    Calls are admitted through the shared queue and traced, but not priced in the budget ledger:
    the subscription pays, and its 5-hour and weekly limits are shared with the user's own claude.ai
    and Claude Code use. Reaching them raises LLMBudgetExceeded until they reset. The CLI checks the
    answer against the JSON schema and asks again on a mismatch; the full schema, including pydantic
    validators, is checked again by `with_structured_output`.
    """

    cli_path: str = "claude"
    oauth_token: SecretStr | None = Field(default=None, exclude=True, repr=False)

    _workdir: str | None = PrivateAttr(default=None)
    _workdir_lock: Any = PrivateAttr(default_factory=threading.Lock)

    @model_validator(mode="after")
    def _check_cli(self) -> "ClaudePlanChat":
        resolved = shutil.which(self.cli_path)
        if resolved is None:
            raise ValueError(f"Claude CLI not found ({self.cli_path}); install Claude Code and run `claude setup-token`")
        version = cli_version(resolved)
        if version < MIN_CLI_VERSION:
            raise ValueError(f"Claude CLI {'.'.join(map(str, version))} is too old; "
                             f"HQA needs {'.'.join(map(str, MIN_CLI_VERSION))} or later")
        return self

    @property
    def _llm_type(self) -> str:
        return "hqa-claude-plan"

    def _workdir_path(self) -> str:
        with self._workdir_lock:
            if self._workdir is None:
                self._workdir = tempfile.mkdtemp(prefix="hqa-claude-plan-")
            return self._workdir

    def _command(self, system: str, schema: Any) -> list[str]:
        command = [self.cli_path, "-p", "--output-format", "json", "--model", self.model, "--effort", self.effort,
                   "--system-prompt", system or DEFAULT_SYSTEM_PROMPT, "--tools", "", "--safe-mode",
                   "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence",
                   "--disable-slash-commands"]
        if schema is not None:
            json_schema = schema if isinstance(schema, dict) else schema.model_json_schema()
            command += ["--json-schema", json.dumps(json_schema, ensure_ascii=False)]
        return command

    def _message(self, completed: subprocess.CompletedProcess, schema: Any) -> AIMessage:
        try:
            data = json.loads(completed.stdout)
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, dict):
            detail = ((completed.stderr or "").strip().splitlines() or [""])[-1][:300]
            raise LLMResponseError(f"Claude CLI exited {completed.returncode} without a result: {detail}")
        text = str(data.get("result") or "")
        status = data.get("api_error_status")
        if data.get("is_error"):
            if status == 401 or "authenticat" in text.lower():
                raise LLMResponseError("Claude subscription login failed; run `claude setup-token` and set "
                                       "CLAUDE_CODE_OAUTH_TOKEN")
            if status == 429 or _LIMIT_PATTERN.search(text):
                raise LLMBudgetExceeded(f"Claude plan usage limit reached: {text[:200]}")
            raise LLMResponseError(f"Claude CLI error{f' ({status})' if status else ''}: {text[:300]}")
        if data.get("subtype") != "success":
            raise LLMResponseError(f"Claude CLI ended with {data.get('subtype')}")

        usage = data.get("usage") or {}
        read = usage.get("cache_read_input_tokens") or 0
        write = usage.get("cache_creation_input_tokens") or 0
        inputs = (usage.get("input_tokens") or 0) + read + write
        outputs = usage.get("output_tokens") or 0
        served = sorted((data.get("modelUsage") or {}).keys())
        stop_reason = data.get("stop_reason")
        message = AIMessage(
            content=text,
            usage_metadata={"input_tokens": inputs, "output_tokens": outputs, "total_tokens": inputs + outputs,
                            "input_token_details": {"cache_read": read, "cache_creation": write}},
            response_metadata={
                "model_name": self.model, "served_models": served, "stop_reason": stop_reason,
                "num_turns": data.get("num_turns"), "duration_ms": data.get("duration_ms"),
                "billing": "claude_subscription", "api_equivalent_cost_usd": data.get("total_cost_usd"),
            },
        )
        add_token_usage_from_response(message)
        if served and self.model not in served:
            logger.warning("Claude %s request asked for %s but was answered by %s", self.hqa_role, self.model, served)
        if stop_reason == "refusal":
            raise LLMResponseError(f"Claude declined the {self.hqa_role} request")
        if stop_reason == "max_tokens":
            raise LLMResponseError("Claude stopped at the output limit; the answer is incomplete")
        if schema is not None:
            structured = data.get("structured_output")
            if not isinstance(structured, (dict, list)):
                raise LLMResponseError("Claude returned no structured output")
            message.content = json.dumps(structured, ensure_ascii=False)
        elif not text.strip():
            raise LLMResponseError("Claude returned no text")
        return message

    def _generate(self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None,
                  **kwargs: Any) -> ChatResult:
        if stop:
            raise ValueError("Stop sequences are not available through the Claude CLI")
        max_tokens, system, turns, schema = self._conversation(messages, kwargs)
        if len(turns) != 1:
            raise ValueError("The Claude CLI takes exactly one user message per call")
        prompt = turns[0]["content"]
        command = self._command(system, schema)
        env = _cli_env(self.oauth_token.get_secret_value() if self.oauth_token else None, max_tokens)
        # No token count is available on the subscription. About three UTF-8 bytes a token is close for
        # Korean text and generous for ASCII JSON; the payload is already fitted to the role's input limit.
        tokens = min(-(-len((system + prompt).encode("utf-8")) // 3), self.hqa_input_limit) + max_tokens

        def run() -> subprocess.CompletedProcess:
            return subprocess.run(command, input=prompt, capture_output=True, text=True, cwd=self._workdir_path(),
                                  env=env, timeout=self.request_timeout + _STARTUP_SECONDS)

        try:
            completed = run_with_llm_slot(run, tokens=tokens)
        except subprocess.TimeoutExpired as error:
            raise LLMResponseError(f"Claude CLI did not answer the {self.hqa_role} request in time") from error
        return ChatResult(generations=[ChatGeneration(message=self._message(completed, schema))])
