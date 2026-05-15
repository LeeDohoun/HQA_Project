from __future__ import annotations

from types import SimpleNamespace

from src.agents.theme_orchestrator import (
    ThemeAnalystEvaluation,
    ThemeLeaderOrchestrator,
)


def _orchestrator() -> ThemeLeaderOrchestrator:
    return ThemeLeaderOrchestrator.__new__(ThemeLeaderOrchestrator)


def test_extract_first_json_object_ignores_trailing_extra_data():
    orchestrator = _orchestrator()

    payload = orchestrator._extract_first_json_object(
        '{"moat_score": 31, "summary": "ok"}\n{"extra": true}'
    )

    assert payload == {"moat_score": 31, "summary": "ok"}


def test_invoke_json_prefers_structured_output_when_available():
    orchestrator = _orchestrator()

    class StructuredRunner:
        def invoke(self, _prompt):
            return ThemeAnalystEvaluation(
                moat_score=34,
                growth_score=20,
                grade="A",
                summary="structured",
                key_points=["point"],
            )

    class StructuredLLM:
        def __init__(self):
            self.method = None
            self.raw_called = False

        def with_structured_output(self, _schema, method="json_schema"):
            self.method = method
            return StructuredRunner()

        def invoke(self, _prompt):
            self.raw_called = True
            return SimpleNamespace(content='{"moat_score": 1}')

    llm = StructuredLLM()
    payload = orchestrator._invoke_json(
        llm,
        "prompt",
        ThemeAnalystEvaluation,
        label="test-structured",
    )

    assert llm.method == "json_schema"
    assert llm.raw_called is False
    assert payload["moat_score"] == 34
    assert payload["summary"] == "structured"


def test_invoke_json_recovers_first_json_object_from_raw_response():
    orchestrator = _orchestrator()

    class RawOnlyLLM:
        def with_structured_output(self, _schema, method="json_schema"):
            raise RuntimeError("structured output unavailable")

        def invoke(self, _prompt):
            return SimpleNamespace(
                content=(
                    "설명 문장\n"
                    "```json\n"
                    '{"moat_score": 28, "growth_score": 19, "grade": "B", "summary": "raw"}'
                    "\n```\n"
                    '{"ignored": true}'
                )
            )

    payload = orchestrator._invoke_json(
        RawOnlyLLM(),
        "prompt",
        ThemeAnalystEvaluation,
        label="test-raw-recovery",
    )

    assert payload["moat_score"] == 28
    assert payload["growth_score"] == 19
    assert payload["summary"] == "raw"


def test_strategy_profile_leader_score_weights():
    orchestrator = _orchestrator()

    default_score = orchestrator._compute_leader_score(
        analyst_total=60,
        quant_total=85,
        chartist_total=95,
        final_total=72,
        data_presence_score=80,
        strategy_profile="default",
    )
    short_score = orchestrator._compute_leader_score(
        analyst_total=60,
        quant_total=85,
        chartist_total=95,
        final_total=72,
        data_presence_score=80,
        strategy_profile="short",
    )
    long_score = orchestrator._compute_leader_score(
        analyst_total=60,
        quant_total=85,
        chartist_total=95,
        final_total=72,
        data_presence_score=80,
        strategy_profile="long",
    )

    assert default_score == round(72 * 0.7 + 80 * 0.3)
    assert short_score > long_score


def test_unknown_strategy_profile_falls_back_to_default():
    orchestrator = _orchestrator()
    assert orchestrator._normalize_strategy_profile("unknown") == "default"
