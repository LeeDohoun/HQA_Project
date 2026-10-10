"""Claude API check before a PAPER run. Prints no key.

The default run costs nothing. It confirms the key and each configured role model with the Models
API (lifecycle, limits, server-side fallback targets and whether HQA prices them), then rebuilds
every specialist request the local data would produce and counts it with the token-counting
endpoint, next to the role's input limit and the offline estimate the payload fitting uses.

--send also makes one real quant request through the production model wrapper (budget ledger
included) and reports its usage, cost, latency, stop reason and the organization's rate limits.

--plan checks the subscription path (LLM_PROVIDER=claude_plan) instead: the Claude CLI version and
its login as HQA starts it, then with --send one real quant request on the subscription.

    venv/bin/python -m scripts.claude_check [--data-dir DIR] [--config config/watchlist.yaml] [--send]
    venv/bin/python -m scripts.claude_check --plan [--data-dir DIR] [--send]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

import anthropic

from src.agents import llm_config
from src.config.settings import load_project_env
from src.runner.analysis_contracts import SpecialistResult
from src.utils.llm_budget import PRICES

ROLES = ("analyst", "quant", "chartist", "risk_manager", "summary")
SPECIALISTS = ("analyst", "quant", "chartist")
FALLBACK_LIST_BETA = "server-side-fallback-2026-06-01"


class _Capture:
    """Stands in for a role model: records the exact request and returns a valid placeholder."""

    reasoning_effort = "low"

    def __init__(self, role: str, captured: list, input_limit: int, output_limit: int):
        self.role, self.captured = role, captured
        self.model_name, self.hqa_input_limit, self.max_tokens = f"capture:{role}", input_limit, output_limit

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, messages):
        payload = json.loads(messages[-1][1])
        self.captured.append((self.role, payload["stock_code"], messages))
        return SpecialistResult.model_validate({
            "stock_code": payload["stock_code"], "role": self.role, "score": 50.0, "confidence": 50,
            "thesis": "capture", "risks": [], "citations": [{"source_id": payload["source_ids"][0], "claim": "capture"}],
            "data_gaps": []})


def check_models(client: anthropic.Anthropic, models: dict[str, str]) -> bool:
    ok = True
    for model_id in sorted(set(models.values())):
        roles = ", ".join(role for role, name in models.items() if name == model_id)
        try:
            info = client.beta.models.retrieve(model_id, betas=[FALLBACK_LIST_BETA])
        except anthropic.AuthenticationError:
            print("FAIL key rejected (401). Create the key in the Console organization linked to the plan.")
            return False
        except anthropic.APIStatusError as error:
            print(f"FAIL {model_id} ({roles}): HTTP {error.status_code} {error.message}")
            ok = False
            continue
        structured = getattr(getattr(info.capabilities, "structured_outputs", None), "supported", None)
        fallbacks = info.allowed_fallback_models or []
        unpriced = [name for name in fallbacks if name not in PRICES]
        retires = info.retires_at.date().isoformat() if info.retires_at else "-"
        print(f"OK   {model_id} ({roles}): {info.display_name}, lifecycle={info.lifecycle}, retires={retires}, "
              f"input<={info.max_input_tokens}, output<={info.max_tokens}, structured_outputs={structured}, "
              f"fallback_targets={fallbacks or '-'}" + (f", UNPRICED={unpriced}" if unpriced else ""))
        ok = ok and info.lifecycle == "active" and bool(structured) and not unpriced
    return ok


def capture_specialist_requests(data_dir: str | None, config_path: str) -> list:
    from src.runner.analysis_data import LocalAnalysisData
    from src.runner.shared_analysis import SharedAnalysisService

    captured: list = []
    models = {}
    for role in (*SPECIALISTS, "risk_manager"):
        limits = llm_config.get_role_limits(role, "anthropic")
        models[role] = _Capture(role, captured, limits.input_tokens, limits.output_tokens)
    data = LocalAnalysisData(config_path=config_path, data_dir=data_dir)
    candidates, errors = data.load_universe(datetime.now(timezone.utc))
    print(f"universe: {len(candidates)} stocks" + (f", loader errors: {errors}" if errors else ""))
    SharedAnalysisService(data=data, accounts=None, models=models).run_cycle([])
    return captured


def count_requests(client: anthropic.Anthropic, models: dict[str, str], captured: list) -> bool:
    from src.runner.shared_analysis import estimate_tokens

    fmt = {"type": "json_schema", "schema": anthropic.transform_schema(SpecialistResult)}
    ok, worst = True, 0.0
    for role, code, messages in sorted(captured, key=lambda item: (item[1], item[0])):
        system, user = messages[0][1], messages[-1][1]
        counted = client.messages.count_tokens(model=models[role], system=system, output_config={"format": fmt},
                                               messages=[{"role": "user", "content": user}]).input_tokens
        limit = llm_config.get_role_limits(role, "anthropic").input_tokens
        estimate = estimate_tokens(system) + estimate_tokens(user)
        worst = max(worst, counted / limit)
        status = "OK  " if counted <= limit else "FAIL"
        ok = ok and counted <= limit
        print(f"{status} {code} {role:9} claude={counted:>6} limit={limit} ({counted / limit:5.1%})"
              f" offline_estimate={estimate:>6} claude/estimate={counted / max(estimate, 1):.2f}")
        time.sleep(0.3)  # token counting has its own rate limit
    if captured:
        print(f"largest specialist request uses {worst:.1%} of its input limit")
    return ok


def send_one(captured: list) -> bool:
    from src.utils.llm_budget import get_llm_budget

    quant = [item for item in captured if item[0] == "quant"]
    if not quant:
        print("SKIP --send: no quant request in the local data")
        return False
    role, code, messages = min(quant, key=lambda item: len(item[2][-1][1]))
    model = llm_config.get_quant_llm()
    budget = get_llm_budget()
    before = budget.snapshot()["spent_usd"]
    started = time.monotonic()
    result = model.with_structured_output(SpecialistResult, method="json_schema", strict=True, include_raw=True).invoke(messages)
    elapsed = time.monotonic() - started
    raw, meta = result["raw"], result["raw"].response_metadata
    usage = raw.usage_metadata
    served = meta.get("served_model") or meta.get("served_models")
    print(f"sent {code} quant on {model.model_name} (effort {model.reasoning_effort}): {elapsed:.1f}s, "
          f"stop={meta['stop_reason']}, served_by={served}, fallback={meta.get('fallback_served', '-')}")
    thinking = (usage.get("output_token_details") or {}).get("reasoning", "-")
    if meta.get("billing") == "claude_subscription":
        cost = f"subscription (API-equivalent ${meta.get('api_equivalent_cost_usd') or 0:.5f})"
    else:
        cost = f"${budget.snapshot()['spent_usd'] - before:.5f}"
    print(f"usage: input={usage['input_tokens']} output={usage['output_tokens']} (thinking {thinking}), cost={cost}")
    if "rate_limits" in meta:
        print("rate limits:", json.dumps(meta["rate_limits"], ensure_ascii=False))
    if result["parsing_error"] is not None:
        print(f"FAIL answer did not validate: {result['parsing_error']}")
        return False
    print(f"OK   answer validated: score={result['parsed'].score}, confidence={result['parsed'].confidence}, "
          f"citations={len(result['parsed'].citations)}")
    return True


def check_plan(args) -> int:
    from src.utils.claude_plan_chat import MIN_CLI_VERSION, _cli_env, cli_version

    cli = shutil.which(os.getenv("HQA_CLAUDE_CLI") or "claude")
    if cli is None:
        print("FAIL Claude CLI not found; install Claude Code (npm install -g @anthropic-ai/claude-code)")
        return 1
    version = cli_version(cli)
    print(f"{'OK  ' if version >= MIN_CLI_VERSION else 'FAIL'} Claude CLI {'.'.join(map(str, version))} at {cli}")
    token = (os.getenv("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
    print(f"long-lived token: {f'set ({len(token)} characters)' if token else 'not set, so the CLI keychain login is used'}")
    status = subprocess.run([cli, "auth", "status"], capture_output=True, text=True, timeout=60, env=_cli_env(token or None))
    try:
        login = json.loads(status.stdout)
    except json.JSONDecodeError:
        login = {}
    print(f"login as HQA starts the CLI: loggedIn={login.get('loggedIn')}, method={login.get('authMethod')}, "
          f"subscription={login.get('subscriptionType')}")
    print("role models:", json.dumps({role: llm_config._claude_model(role) for role in ROLES}))
    ok = version >= MIN_CLI_VERSION and bool(login.get("loggedIn"))
    if ok and args.send:
        ok = send_one(capture_specialist_requests(args.data_dir, args.config))
    print("RESULT", "OK" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", help="Collected data root (default: HQA_DATA_DIR)")
    parser.add_argument("--config", default="config/watchlist.yaml")
    parser.add_argument("--send", action="store_true", help="Also make one real quant request")
    parser.add_argument("--plan", action="store_true", help="Check the Claude subscription path (claude_plan)")
    args = parser.parse_args(argv)
    load_project_env()
    if args.plan:
        os.environ["LLM_PROVIDER"] = "claude_plan"
        return check_plan(args)
    os.environ["LLM_PROVIDER"] = "anthropic"
    key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        print("FAIL ANTHROPIC_API_KEY is not set")
        return 1
    print(f"key: set ({len(key)} characters)")
    models = {role: llm_config._claude_model(role) for role in ROLES}
    client = anthropic.Anthropic(api_key=key, base_url=llm_config.CLAUDE_BASE_URL, max_retries=0, timeout=60)
    ok = check_models(client, models)
    if not ok:
        return 1
    captured = capture_specialist_requests(args.data_dir, args.config)
    ok = count_requests(client, models, captured) and ok
    if args.send:
        ok = send_one(captured) and ok
    print("RESULT", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
