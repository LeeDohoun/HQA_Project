import json
import logging
import stat
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

from src.utils.llm_budget import LLMBudgetExceeded, get_llm_budget
from src.utils.llm_errors import LLMResponseError

PLAN_ENV = ("HQA_CLAUDE_MODEL", "HQA_CLAUDE_QUANT_MODEL", "HQA_CLAUDE_QUANT_EFFORT", "HQA_CLAUDE_RISK_MANAGER_MODEL",
            "HQA_LLM_TIMEOUT_SECONDS", "HQA_LLM_QUANT_TIMEOUT_SECONDS", "HQA_LLM_QUANT_MAX_OUTPUT_TOKENS",
            "HQA_LLM_RISK_MANAGER_MAX_OUTPUT_TOKENS", "ANTHROPIC_AUTH_TOKEN")

FAKE_CLI = '''#!{python}
import json, os, sys, time
from pathlib import Path
here = Path(__file__).parent
if "--version" in sys.argv:
    print((here / "version.txt").read_text().strip() + " (Claude Code)")
    raise SystemExit(0)
record = {{"argv": sys.argv[1:], "stdin": sys.stdin.read(), "cwd": os.getcwd(),
           "env": {{key: value for key, value in os.environ.items()
                    if key.startswith(("ANTHROPIC", "CLAUDE", "DISABLE", "HQA", "KIS"))}}}}
with (here / "calls.jsonl").open("a") as log:
    log.write(json.dumps(record) + "\\n")
reply = json.loads((here / "reply.json").read_text())
time.sleep(reply.pop("_sleep", 0))
exit_code = reply.pop("_exit", 0)
print(reply.pop("_raw") if "_raw" in reply else json.dumps(reply))
raise SystemExit(exit_code)
'''


class Analysis(BaseModel):
    verdict: str


def result(**overrides):
    body = {"type": "result", "subtype": "success", "is_error": False, "duration_ms": 1200, "num_turns": 1,
            "result": "", "stop_reason": "end_turn", "total_cost_usd": 0.0123,
            "usage": {"input_tokens": 90, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 10,
                      "output_tokens": 30},
            "modelUsage": {"claude-opus-5-5": {"inputTokens": 90, "outputTokens": 30}},
            "structured_output": {"verdict": "HOLD"}}
    body.update(overrides)
    return body


class FakeCli:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "claude"
        self.path.write_text(FAKE_CLI.format(python=sys.executable), encoding="utf-8")
        self.path.chmod(self.path.stat().st_mode | stat.S_IEXEC)
        self.version("2.1.296")
        self.reply(result())

    def version(self, value):
        (self.root / "version.txt").write_text(value, encoding="utf-8")

    def reply(self, body):
        (self.root / "reply.json").write_text(json.dumps(body), encoding="utf-8")

    def calls(self):
        log = self.root / "calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def option(argv, name):
    return argv[argv.index(name) + 1]


@pytest.fixture
def fake_cli(monkeypatch, tmp_path):
    for name in PLAN_ENV:
        monkeypatch.delenv(name, raising=False)
    cli = FakeCli(tmp_path / "cli")
    monkeypatch.setenv("LLM_PROVIDER", "claude_plan")
    monkeypatch.setenv("HQA_CLAUDE_CLI", str(cli.path))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "offline-plan-token")
    # Neither may reach the CLI: an API key would bill API credits instead of the subscription.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-reach-the-cli")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.invalid")
    monkeypatch.setenv("HQA_INTERNAL_TOKEN", "must-not-reach-the-cli")
    monkeypatch.setenv("HQA_LLM_BUDGET_PATH", str(tmp_path / "spend.sqlite3"))
    return cli


@pytest.fixture(autouse=True)
def fresh_version_cache():
    from src.utils.claude_plan_chat import cli_version

    cli_version.cache_clear()
    yield
    cli_version.cache_clear()


def test_structured_output_runs_the_cli_with_every_customization_off(fake_cli):
    from src.agents.llm_config import get_quant_llm

    model = get_quant_llm()
    parsed = model.with_structured_output(Analysis, method="json_schema", strict=True).invoke(
        [("system", "quant instructions"), ("human", "Analyze the supplied data")])
    assert parsed == Analysis(verdict="HOLD")
    [call] = fake_cli.calls()
    argv = call["argv"]
    assert argv[:3] == ["-p", "--output-format", "json"]
    assert option(argv, "--model") == "claude-opus-5-5" and option(argv, "--effort") == "low"
    assert option(argv, "--system-prompt") == "quant instructions"
    assert option(argv, "--tools") == "" and option(argv, "--setting-sources") == ""
    for flag in ("--safe-mode", "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands"):
        assert flag in argv
    assert json.loads(option(argv, "--json-schema"))["properties"]["verdict"]["type"] == "string"
    assert call["stdin"] == "Analyze the supplied data"
    assert call["env"] == {"CLAUDE_CODE_OAUTH_TOKEN": "offline-plan-token", "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "4000",
                           "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "DISABLE_AUTOUPDATER": "1"}
    assert "hqa-claude-plan-" in call["cwd"] and Path(call["cwd"]).resolve() != Path.cwd().resolve()
    # The subscription pays: nothing is reserved or spent in the API budget ledger.
    assert get_llm_budget().snapshot()["models"] == []


def test_plain_invoke_returns_text_usage_and_the_answering_model(fake_cli):
    from src.agents.llm_config import get_quant_llm

    fake_cli.reply(result(result="요약", structured_output=None))
    message = get_quant_llm().invoke("Summarize")
    assert message.content == "요약"
    assert message.usage_metadata["input_tokens"] == 100 and message.usage_metadata["output_tokens"] == 30
    meta = message.response_metadata
    assert meta["served_models"] == ["claude-opus-5-5"] and meta["billing"] == "claude_subscription"
    assert meta["api_equivalent_cost_usd"] == 0.0123
    assert option(fake_cli.calls()[0]["argv"], "--system-prompt") == "Answer the request directly."
    assert "--json-schema" not in fake_cli.calls()[0]["argv"]


def test_a_reached_plan_limit_is_a_budget_rejection(fake_cli):
    from src.agents.llm_config import get_quant_llm

    fake_cli.reply(result(is_error=True, result="You've hit your limit · resets 3pm (Asia/Seoul)", _exit=1))
    with pytest.raises(LLMBudgetExceeded, match="plan usage limit"):
        get_quant_llm().invoke("Analyze")


def test_an_expired_login_names_the_fix(fake_cli):
    from src.agents.llm_config import get_quant_llm

    fake_cli.reply(result(is_error=True, api_error_status=401, _exit=1,
                          result="Failed to authenticate. API Error: 401 OAuth access token has expired."))
    with pytest.raises(LLMResponseError, match="setup-token"):
        get_quant_llm().invoke("Analyze")


@pytest.mark.parametrize("overrides, message", [
    ({"subtype": "error_max_structured_output_retries"}, "error_max_structured_output_retries"),
    ({"structured_output": None}, "no structured output"),
    ({"stop_reason": "max_tokens"}, "output limit"),
    ({"stop_reason": "refusal"}, "declined"),
    ({"_raw": "Error: unexpected crash", "_exit": 2}, "exited 2"),
])
def test_unusable_answers_are_rejected(fake_cli, overrides, message):
    from src.agents.llm_config import get_quant_llm

    fake_cli.reply(result(**overrides))
    with pytest.raises(LLMResponseError, match=message):
        get_quant_llm().with_structured_output(Analysis).invoke("Analyze")


def test_a_schema_mismatch_after_the_cli_still_fails_validation(fake_cli):
    from src.agents.llm_config import get_quant_llm

    fake_cli.reply(result(structured_output={"unexpected": "HOLD"}))
    with pytest.raises(ValueError):
        get_quant_llm().with_structured_output(Analysis).invoke("Analyze")


def test_a_slow_cli_is_stopped(fake_cli, monkeypatch):
    from src.utils import claude_plan_chat
    from src.agents.llm_config import get_quant_llm

    monkeypatch.setattr(claude_plan_chat, "_STARTUP_SECONDS", 0.0)
    monkeypatch.setenv("HQA_LLM_QUANT_TIMEOUT_SECONDS", "0.5")
    fake_cli.reply(result(_sleep=5))
    with pytest.raises(LLMResponseError, match="in time"):
        get_quant_llm().invoke("Analyze")


def test_another_answering_model_is_logged(fake_cli, caplog):
    from src.agents.llm_config import get_quant_llm

    fake_cli.reply(result(modelUsage={"claude-sonnet-5-5": {"inputTokens": 90, "outputTokens": 30}}))
    with caplog.at_level(logging.WARNING, logger="src.utils.claude_plan_chat"):
        assert get_quant_llm().with_structured_output(Analysis).invoke("Analyze").verdict == "HOLD"
    assert "answered by ['claude-sonnet-5-5']" in caplog.text


def test_old_or_missing_clis_are_refused(fake_cli, monkeypatch):
    from src.agents.llm_config import get_quant_llm

    fake_cli.version("2.1.147")
    with pytest.raises(ValueError, match="too old"):
        get_quant_llm()
    monkeypatch.setenv("HQA_CLAUDE_CLI", str(fake_cli.root / "missing-claude"))
    with pytest.raises(ValueError, match="not found"):
        get_quant_llm()


@pytest.mark.parametrize("messages, kwargs", [
    ([("human", "a"), ("ai", "b"), ("human", "c")], {}),
    ("Analyze", {"max_tokens": 4001}),
    ("Analyze", {"model": "claude-haiku-5-5"}),
    ("Analyze", {"stop": ["END"]}),
])
def test_requests_the_cli_cannot_carry_are_refused_before_it_runs(fake_cli, messages, kwargs):
    from src.agents.llm_config import get_quant_llm

    with pytest.raises(ValueError):
        get_quant_llm().invoke(messages, **kwargs)
    assert fake_cli.calls() == []


def test_the_token_never_appears_in_the_model_repr(fake_cli):
    from src.agents.llm_config import get_quant_llm

    assert "offline-plan-token" not in repr(get_quant_llm())


def test_risk_manager_decision_round_trips_through_the_cli_offline(fake_cli):
    from datetime import datetime, timedelta, timezone

    from src.agents.llm_config import get_risk_manager_llm
    from src.runner.analysis_contracts import AccountDecision

    now = datetime(2026, 10, 12, 1, tzinfo=timezone.utc)
    held = {"stock_code": "000001", "stock_name": "Stock 000001", "action": "HOLD", "holding_quantity": 4,
            "confidence": 70, "risk_level": "MEDIUM", "position_size_pct": 0.0, "entry_price": None,
            "stop_loss_price": 95.0, "take_profit_price": None,
            "entry_valid_until": (now + timedelta(minutes=10)).isoformat(), "planned_exit_at": (now + timedelta(days=5)).isoformat(),
            "condition_payload": {"schema_version": 2, "entry_conditions": [], "reduce_conditions": [], "invalidation_conditions": [],
                                  "exit_conditions": [{"id": "stop", "all": [{"field": "current_price", "operator": "<=", "value": 95.0}]}]},
            "citations": [{"source_id": "quote:000001", "claim": "current quote"}], "reasoning": "keep the stop"}
    broken = {**held, "stock_code": "000002", "stock_name": "Stock 000002", "holding_quantity": 0,
              "planned_exit_at": held["entry_valid_until"]}
    fake_cli.reply(result(structured_output={"plans": [held, broken], "reasoning": "r"}))
    decision = get_risk_manager_llm().with_structured_output(AccountDecision, method="json_schema", strict=True).invoke(
        [("system", "RiskManager instructions"), ("human", "{}")])
    assert [plan.stock_code for plan in decision.plans] == ["000001"]
    assert [row["stock_code"] for row in decision.invalid_plans] == ["000002"]
    argv = fake_cli.calls()[0]["argv"]
    assert option(argv, "--effort") == "medium" and fake_cli.calls()[0]["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "16000"
    assert '"format": "date-time"' in option(argv, "--json-schema")  # needs CLI 2.1.205 or later
