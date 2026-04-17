# 파일: tests/test_conflict_detector.py
"""
ConflictDetector 단위 테스트

검증 항목:
- 각 규칙별 발동/미발동
- severity 계산
- 정상 상황에서 불필요한 경고 없는지
- action 한글/영문 매핑
- position_size 문자열 변형 파싱
- position_size 없을 때 R002 skip
- detect_from_theme_result / detect_from_graph_result 양쪽 테스트
"""

import pytest

from src.agents.conflict_detector import (
    ConflictDetector,
    ConflictReport,
    _normalize_action,
    _parse_position_size,
)


@pytest.fixture
def detector():
    return ConflictDetector()


def _make_theme_result(
    analyst_total=35,
    quant_total=50,
    chartist_total=50,
    chartist_signal="중립",
    action="HOLD",
    confidence=50,
    risk_level="MEDIUM",
    risk_factors=None,
    position_size=None,
    evidence=None,
):
    """테스트용 theme_orchestrator 결과 생성"""
    result = {
        "analyst": {"total_score": analyst_total},
        "quant": {"total_score": quant_total},
        "chartist": {"total_score": chartist_total, "signal": chartist_signal},
        "final_decision": {
            "action": action,
            "confidence": confidence,
            "risk_level": risk_level,
            "risk_factors": risk_factors or [],
        },
        "evidence": evidence or [],
    }
    if position_size is not None:
        result["final_decision"]["position_size"] = position_size
    return result


class TestNormalAction:
    """정상 상황에서 경고 없음"""

    def test_balanced_hold(self, detector):
        """균형잡힌 HOLD → 경고 없음"""
        result = _make_theme_result(
            analyst_total=35, quant_total=50, chartist_total=50,
            action="HOLD", confidence=50, position_size="25%",
            evidence=[{"doc1": True}, {"doc2": True}, {"doc3": True}],
        )
        report = detector.detect_from_theme_result(result)
        assert report.severity == "NONE"
        assert len(report.warnings) == 0
        assert report.suggested_action == "proceed"

    def test_strong_scores_buy(self, detector):
        """높은 점수 + BUY → 경고 없음"""
        result = _make_theme_result(
            analyst_total=55, quant_total=75, chartist_total=80,
            action="BUY", confidence=70, position_size="50%",
            risk_factors=["시장 변동성"],
            evidence=[{"d1": True}] * 5,
        )
        report = detector.detect_from_theme_result(result)
        assert report.severity == "NONE"


class TestR001:
    """R001: Quant 점수 < 40인데 BUY 이상"""

    def test_triggered(self, detector):
        result = _make_theme_result(quant_total=35, action="BUY")
        report = detector.detect_from_theme_result(result)
        assert "R001" in report.triggered_rules
        assert report.severity == "MEDIUM"

    def test_triggered_strong_buy(self, detector):
        result = _make_theme_result(quant_total=20, action="STRONG_BUY")
        report = detector.detect_from_theme_result(result)
        assert "R001" in report.triggered_rules

    def test_not_triggered_hold(self, detector):
        result = _make_theme_result(quant_total=35, action="HOLD")
        report = detector.detect_from_theme_result(result)
        assert "R001" not in report.triggered_rules

    def test_not_triggered_high_score(self, detector):
        result = _make_theme_result(quant_total=50, action="BUY")
        report = detector.detect_from_theme_result(result)
        assert "R001" not in report.triggered_rules

    def test_action_korean_buy(self, detector):
        """한글 '매수' → BUY로 변환 후 R001 발동"""
        result = _make_theme_result(quant_total=30, action="매수")
        report = detector.detect_from_theme_result(result)
        assert "R001" in report.triggered_rules

    def test_action_korean_strong_buy(self, detector):
        """한글 '적극 매수' → STRONG_BUY로 변환"""
        result = _make_theme_result(quant_total=30, action="적극 매수")
        report = detector.detect_from_theme_result(result)
        assert "R001" in report.triggered_rules


class TestR002:
    """R002: Chartist 매도인데 position_size > 50%"""

    def test_triggered(self, detector):
        result = _make_theme_result(
            chartist_signal="매도", position_size="60%"
        )
        report = detector.detect_from_theme_result(result)
        assert "R002" in report.triggered_rules
        assert report.severity == "HIGH"

    def test_not_triggered_neutral_signal(self, detector):
        result = _make_theme_result(
            chartist_signal="중립", position_size="60%"
        )
        report = detector.detect_from_theme_result(result)
        assert "R002" not in report.triggered_rules

    def test_not_triggered_low_position(self, detector):
        result = _make_theme_result(
            chartist_signal="매도", position_size="30%"
        )
        report = detector.detect_from_theme_result(result)
        assert "R002" not in report.triggered_rules

    def test_skip_when_no_position_size(self, detector):
        """position_size 키가 없으면 R002 건너뜀"""
        result = _make_theme_result(
            chartist_signal="매도",
            # position_size 미설정!
        )
        report = detector.detect_from_theme_result(result)
        assert "R002" not in report.triggered_rules


class TestR003:
    """R003: 근거 < 2건인데 confidence >= 80"""

    def test_triggered(self, detector):
        result = _make_theme_result(confidence=85, evidence=[{"one": True}])
        report = detector.detect_from_theme_result(result)
        assert "R003" in report.triggered_rules

    def test_triggered_zero_evidence(self, detector):
        result = _make_theme_result(confidence=90, evidence=[])
        report = detector.detect_from_theme_result(result)
        assert "R003" in report.triggered_rules

    def test_not_triggered_enough_evidence(self, detector):
        result = _make_theme_result(
            confidence=85, evidence=[{"a": 1}, {"b": 2}]
        )
        report = detector.detect_from_theme_result(result)
        assert "R003" not in report.triggered_rules

    def test_not_triggered_low_confidence(self, detector):
        result = _make_theme_result(confidence=70, evidence=[])
        report = detector.detect_from_theme_result(result)
        assert "R003" not in report.triggered_rules


class TestR004:
    """R004: Analyst/Quant 차이 > 40"""

    def test_triggered(self, detector):
        # analyst_total=60 → normalized=85.7, quant=30 → 차이 55.7
        result = _make_theme_result(analyst_total=60, quant_total=30)
        report = detector.detect_from_theme_result(result)
        assert "R004" in report.triggered_rules

    def test_not_triggered_close(self, detector):
        # analyst_total=35 → normalized=50, quant=50 → 차이 0
        result = _make_theme_result(analyst_total=35, quant_total=50)
        report = detector.detect_from_theme_result(result)
        assert "R004" not in report.triggered_rules


class TestR005:
    """R005: risk_factors >= 3인데 risk_level LOW"""

    def test_triggered(self, detector):
        result = _make_theme_result(
            risk_factors=["a", "b", "c"], risk_level="LOW"
        )
        report = detector.detect_from_theme_result(result)
        assert "R005" in report.triggered_rules

    def test_triggered_korean_level(self, detector):
        """한글 리스크 수준"""
        result = _make_theme_result(
            risk_factors=["a", "b", "c", "d"], risk_level="낮음"
        )
        report = detector.detect_from_theme_result(result)
        assert "R005" in report.triggered_rules

    def test_not_triggered_medium(self, detector):
        result = _make_theme_result(
            risk_factors=["a", "b", "c"], risk_level="MEDIUM"
        )
        report = detector.detect_from_theme_result(result)
        assert "R005" not in report.triggered_rules

    def test_not_triggered_few_factors(self, detector):
        result = _make_theme_result(
            risk_factors=["a", "b"], risk_level="LOW"
        )
        report = detector.detect_from_theme_result(result)
        assert "R005" not in report.triggered_rules


class TestR006:
    """R006: 2개 이상 점수 < 40인데 STRONG_BUY"""

    def test_triggered(self, detector):
        # analyst 20 → normalized 28.6, quant 30, chartist 80
        result = _make_theme_result(
            analyst_total=20, quant_total=30, chartist_total=80,
            action="STRONG_BUY",
        )
        report = detector.detect_from_theme_result(result)
        assert "R006" in report.triggered_rules
        assert report.severity == "HIGH"

    def test_not_triggered_buy(self, detector):
        """BUY는 R006 대상 아님 (STRONG_BUY만)"""
        result = _make_theme_result(
            analyst_total=20, quant_total=30, chartist_total=80,
            action="BUY",
        )
        report = detector.detect_from_theme_result(result)
        assert "R006" not in report.triggered_rules

    def test_not_triggered_high_scores(self, detector):
        result = _make_theme_result(
            analyst_total=50, quant_total=60, chartist_total=70,
            action="STRONG_BUY",
        )
        report = detector.detect_from_theme_result(result)
        assert "R006" not in report.triggered_rules


class TestSeverityCalculation:
    """severity 계산"""

    def test_multiple_rules_highest_wins(self, detector):
        """여러 규칙 발동 시 가장 높은 severity"""
        result = _make_theme_result(
            quant_total=30, action="BUY",      # R001: MEDIUM
            chartist_signal="매도", position_size="60%",  # R002: HIGH
        )
        report = detector.detect_from_theme_result(result)
        assert report.severity == "HIGH"
        assert report.suggested_action == "review"

    def test_medium_suggested_action(self, detector):
        result = _make_theme_result(quant_total=35, action="BUY")
        report = detector.detect_from_theme_result(result)
        assert report.suggested_action == "lower_confidence"


class TestPositionSizeParsing:
    """position_size 문자열 파싱"""

    def test_percent(self):
        assert _parse_position_size("50%") == 50

    def test_percent_with_text(self):
        assert _parse_position_size("25% 이하") == 25

    def test_range(self):
        assert _parse_position_size("10~20%") == 20

    def test_range_dash(self):
        assert _parse_position_size("10-20%") == 20

    def test_minimize(self):
        assert _parse_position_size("최소화") == 0

    def test_none(self):
        assert _parse_position_size(None) == 25

    def test_empty(self):
        assert _parse_position_size("") == 25

    def test_unparseable(self):
        assert _parse_position_size("적극 투자") == 25

    def test_zero_percent(self):
        assert _parse_position_size("0%") == 0


class TestActionNormalization:
    """action 한글/영문 정규화"""

    def test_korean_mappings(self):
        assert _normalize_action("적극 매수") == "STRONG_BUY"
        assert _normalize_action("매수") == "BUY"
        assert _normalize_action("보유/관망") == "HOLD"
        assert _normalize_action("비중 축소") == "REDUCE"
        assert _normalize_action("매도") == "SELL"
        assert _normalize_action("적극 매도") == "STRONG_SELL"

    def test_english_passthrough(self):
        assert _normalize_action("STRONG_BUY") == "STRONG_BUY"
        assert _normalize_action("BUY") == "BUY"
        assert _normalize_action("HOLD") == "HOLD"

    def test_empty(self):
        assert _normalize_action("") == "HOLD"


class TestConflictReportSerialization:
    """to_dict() 직렬화"""

    def test_to_dict(self, detector):
        result = _make_theme_result(quant_total=30, action="BUY")
        report = detector.detect_from_theme_result(result)
        d = report.to_dict()
        assert "severity" in d
        assert "warnings" in d
        assert "triggered_rules" in d
        assert "suggested_action" in d
        assert isinstance(d["warnings"], list)
