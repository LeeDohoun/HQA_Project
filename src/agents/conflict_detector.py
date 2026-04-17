# 파일: src/agents/conflict_detector.py
"""
규칙 기반 충돌 감지 (ConflictDetector)

에이전트 결과 간 논리적 불일치를 자동으로 감지합니다.
결과를 수정하지 않고, 경고만 추가합니다.

규칙 세트 (6개):
- R001: Quant 점수 < 40인데 최종 판단이 BUY 이상
- R002: Chartist 신호가 매도인데 position_size > 50%
- R003: 근거 문서 수 < 2인데 confidence >= 80
- R004: Analyst와 Quant 점수 차이 > 40점 (정규화 기준)
- R005: risk_factors >= 3개인데 risk_level이 LOW/VERY_LOW
- R006: 3 에이전트 중 2개 점수가 하위 40%인데 최종 STRONG_BUY

사용 예시:
    detector = ConflictDetector()

    # theme_orchestrator 결과용
    report = detector.detect_from_theme_result(evaluation_dict)

    # graph.py 결과용
    report = detector.detect_from_graph_result(scores, final_decision, evidence_count=5)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.agents.risk_manager import AgentScores, FinalDecision

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Action 한글/영문 매핑
# ──────────────────────────────────────────────

# 규칙 판정은 영문 기준으로 통일
_ACTION_MAP = {
    "적극 매수": "STRONG_BUY",
    "매수": "BUY",
    "보유/관망": "HOLD",
    "비중 축소": "REDUCE",
    "매도": "SELL",
    "적극 매도": "STRONG_SELL",
}

# BUY 이상 판정용 집합
_BUY_OR_ABOVE = frozenset({"BUY", "STRONG_BUY"})

# 매도 신호 판정용 집합
_SELL_SIGNALS = frozenset({"매도", "SELL", "적극 매도", "STRONG_SELL"})

# LOW 이하 리스크 수준
_LOW_RISK_LEVELS = frozenset({"LOW", "VERY_LOW", "낮음", "매우 낮음"})


def _normalize_action(raw: str) -> str:
    """
    action 문자열을 영문 대문자로 정규화

    한글 입력은 매핑 테이블로 변환, 영문 입력은 그대로 대문자화
    """
    if not raw:
        return "HOLD"
    mapped = _ACTION_MAP.get(raw)
    if mapped:
        return mapped
    return raw.upper().replace(" ", "_")


def _parse_position_size(raw: Any) -> int:
    """
    position_size 문자열을 정수(%)로 파싱

    예시:
        "50%"      → 50
        "25% 이하"  → 25
        "10~20%"   → 20  (범위면 상한)
        "최소화"    → 0
        파싱 실패   → 25  (기본값)
    """
    if raw is None:
        return 25

    text = str(raw).strip()
    if not text:
        return 25

    # "최소화", "없음" 등 → 0으로 처리
    if any(keyword in text for keyword in ("최소", "없음")):
        return 0
    # 정확히 "0%"인 경우
    if text == "0%":
        return 0

    # 범위 패턴: "10~20%", "10-20%"
    range_match = re.search(r"(\d+)\s*[~\-]\s*(\d+)", text)
    if range_match:
        return int(range_match.group(2))  # 상한 사용

    # 단일 숫자 패턴: "50%", "25% 이하", "75"
    num_match = re.search(r"(\d+)", text)
    if num_match:
        return int(num_match.group(1))

    return 25  # 파싱 실패 기본값


# ──────────────────────────────────────────────
# 데이터 구조
# ──────────────────────────────────────────────

@dataclass
class _NormalizedInput:
    """규칙 검사를 위한 정규화된 입력"""
    # Analyst 점수 (0~100 정규화, analyst_total/70*100)
    analyst_total_normalized: float = 50.0
    # Quant 점수 (0~100)
    quant_total: int = 50
    # Chartist 점수 (0~100)
    chartist_total: int = 50
    # Chartist 신호 원본 (매수/중립/매도 등)
    chartist_signal: str = ""
    # 최종 action (영문 정규화)
    final_action: str = "HOLD"
    # 최종 확신도
    final_confidence: int = 50
    # 리스크 수준 원본
    risk_level: str = "MEDIUM"
    # 리스크 요인 수
    risk_factor_count: int = 0
    # position_size 정수(%)
    position_size_pct: int = 25
    # position_size 가용 여부 (없으면 R002 skip)
    position_size_available: bool = False
    # 근거 문서 수
    evidence_count: int = 0


@dataclass
class ConflictReport:
    """충돌 감지 결과"""
    # 최대 심각도: "NONE" / "LOW" / "MEDIUM" / "HIGH"
    severity: str = "NONE"
    # 감지된 충돌 메시지
    warnings: List[str] = field(default_factory=list)
    # 발동된 규칙 ID
    triggered_rules: List[str] = field(default_factory=list)
    # 권장 행동: "proceed" / "review" / "lower_confidence"
    suggested_action: str = "proceed"

    def to_dict(self) -> Dict[str, Any]:
        """직렬화"""
        return {
            "severity": self.severity,
            "warnings": self.warnings,
            "triggered_rules": self.triggered_rules,
            "suggested_action": self.suggested_action,
        }


# ──────────────────────────────────────────────
# ConflictDetector
# ──────────────────────────────────────────────

# 심각도 우선순위
_SEVERITY_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}


class ConflictDetector:
    """
    규칙 기반 충돌 감지기

    에이전트 결과 간 논리적 불일치를 6개 규칙으로 검사합니다.
    결과를 자동 수정하지 않고, 경고만 추가합니다.
    """

    def detect_from_theme_result(self, evaluation: Dict[str, Any]) -> ConflictReport:
        """
        theme_orchestrator.evaluate_candidate() 결과에서 충돌 감지

        theme 결과의 final_decision에 position_size가 없으면
        R002 규칙은 건너뜁니다.

        Args:
            evaluation: evaluate_candidate() 반환 딕셔너리

        Returns:
            ConflictReport
        """
        analyst = evaluation.get("analyst", {})
        quant = evaluation.get("quant", {})
        chartist = evaluation.get("chartist", {})
        decision = evaluation.get("final_decision", {})
        evidence = evaluation.get("evidence", [])

        # position_size 가용 여부
        position_size_raw = decision.get("position_size")
        has_position = position_size_raw is not None

        normalized = _NormalizedInput(
            analyst_total_normalized=round(
                analyst.get("total_score", 35) / 70 * 100, 1
            ),
            quant_total=quant.get("total_score", 50),
            chartist_total=chartist.get("total_score", 50),
            chartist_signal=chartist.get("signal", ""),
            final_action=_normalize_action(decision.get("action", "HOLD")),
            final_confidence=decision.get("confidence", 50),
            risk_level=decision.get("risk_level", "MEDIUM"),
            risk_factor_count=len(decision.get("risk_factors", [])),
            position_size_pct=_parse_position_size(position_size_raw) if has_position else 25,
            position_size_available=has_position,
            evidence_count=len(evidence) if isinstance(evidence, list) else 0,
        )

        return self._check_rules(normalized)

    def detect_from_graph_result(
        self,
        scores: "AgentScores",
        final_decision: "FinalDecision",
        evidence_count: int = 0,
    ) -> ConflictReport:
        """
        graph.py (LangGraph/fallback) 결과에서 충돌 감지

        Args:
            scores: AgentScores dataclass
            final_decision: FinalDecision dataclass
            evidence_count: 근거 문서 수

        Returns:
            ConflictReport
        """
        # FinalDecision.action은 InvestmentAction enum → .value로 변환
        action_raw = getattr(final_decision.action, "value", str(final_decision.action))
        risk_raw = getattr(final_decision.risk_level, "value", str(final_decision.risk_level))

        normalized = _NormalizedInput(
            analyst_total_normalized=round(scores.analyst_total / 70 * 100, 1),
            quant_total=scores.quant_total,
            chartist_total=scores.chartist_total,
            chartist_signal=scores.chartist_signal,
            final_action=_normalize_action(action_raw),
            final_confidence=final_decision.confidence,
            risk_level=risk_raw,
            risk_factor_count=len(final_decision.risk_factors),
            position_size_pct=_parse_position_size(final_decision.position_size),
            position_size_available=True,
            evidence_count=evidence_count,
        )

        return self._check_rules(normalized)

    def _check_rules(self, inp: _NormalizedInput) -> ConflictReport:
        """
        6개 규칙을 순서대로 검사하여 ConflictReport 생성

        각 규칙은 독립적으로 검사됩니다.
        """
        report = ConflictReport()
        max_severity = "NONE"

        # R001: Quant 점수 < 40인데 최종 판단이 BUY 이상
        if inp.quant_total < 40 and inp.final_action in _BUY_OR_ABOVE:
            report.warnings.append(
                f"Quant 점수가 {inp.quant_total}점인데 최종 판단이 {inp.final_action}입니다."
            )
            report.triggered_rules.append("R001")
            max_severity = self._higher_severity(max_severity, "MEDIUM")

        # R002: Chartist 신호가 매도인데 position_size > 50%
        # position_size가 없으면 이 규칙은 건너뜀
        if inp.position_size_available:
            if (inp.chartist_signal in _SELL_SIGNALS and inp.position_size_pct > 50):
                report.warnings.append(
                    f"Chartist 신호가 {inp.chartist_signal}인데 "
                    f"포지션 비중이 {inp.position_size_pct}%입니다."
                )
                report.triggered_rules.append("R002")
                max_severity = self._higher_severity(max_severity, "HIGH")

        # R003: 근거 문서 수 < 2인데 confidence >= 80
        if inp.evidence_count < 2 and inp.final_confidence >= 80:
            report.warnings.append(
                f"근거 문서가 {inp.evidence_count}건인데 확신도가 {inp.final_confidence}%입니다."
            )
            report.triggered_rules.append("R003")
            max_severity = self._higher_severity(max_severity, "MEDIUM")

        # R004: Analyst와 Quant 점수 차이 > 40점 (정규화 기준)
        score_diff = abs(inp.analyst_total_normalized - inp.quant_total)
        if score_diff > 40:
            report.warnings.append(
                f"Analyst({inp.analyst_total_normalized:.0f}점)와 "
                f"Quant({inp.quant_total}점) 점수 차이가 {score_diff:.0f}점입니다."
            )
            report.triggered_rules.append("R004")
            max_severity = self._higher_severity(max_severity, "LOW")

        # R005: risk_factors >= 3개인데 risk_level이 LOW/VERY_LOW
        if inp.risk_factor_count >= 3 and inp.risk_level in _LOW_RISK_LEVELS:
            report.warnings.append(
                f"리스크 요인이 {inp.risk_factor_count}개인데 "
                f"리스크 수준이 {inp.risk_level}입니다."
            )
            report.triggered_rules.append("R005")
            max_severity = self._higher_severity(max_severity, "MEDIUM")

        # R006: 3 에이전트 중 2개 점수가 하위 40%인데 최종 STRONG_BUY
        low_scores = sum(1 for s in [
            inp.analyst_total_normalized,
            inp.quant_total,
            inp.chartist_total,
        ] if s < 40)
        if low_scores >= 2 and inp.final_action == "STRONG_BUY":
            report.warnings.append(
                f"3 에이전트 중 {low_scores}개 점수가 40점 미만인데 "
                f"최종 판단이 STRONG_BUY입니다."
            )
            report.triggered_rules.append("R006")
            max_severity = self._higher_severity(max_severity, "HIGH")

        # 최종 severity 및 suggested_action 결정
        report.severity = max_severity
        if max_severity == "HIGH":
            report.suggested_action = "review"
        elif max_severity == "MEDIUM":
            report.suggested_action = "lower_confidence"
        else:
            report.suggested_action = "proceed"

        return report

    @staticmethod
    def _higher_severity(current: str, candidate: str) -> str:
        """더 높은 심각도 반환"""
        if _SEVERITY_RANK.get(candidate, 0) > _SEVERITY_RANK.get(current, 0):
            return candidate
        return current
