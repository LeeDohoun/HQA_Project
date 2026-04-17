# 파일: tests/test_candidate_filter.py
"""
CandidateFilter 단위 테스트

검증 항목:
- 등급 분류 정확성 (FULL/QUICK/WATCH)
- 경계값 테스트
- 데이터 부족 후보 WATCH_ONLY 분류
- 빈 후보 처리
- 모든 후보가 결과에 포함되는지
- quick_candidate_limit 동작
- apply_limit=True 기존 동작 호환 (theme_orchestrator 테스트)
- FULL이 부족할 때 QUICK 승격 정책
"""

from dataclasses import dataclass

import pytest

from src.agents.candidate_filter import (
    CandidateFilter,
    CandidateFilterConfig,
    CandidateFilterResult,
    TIER_FULL,
    TIER_QUICK,
    TIER_WATCH,
)


@dataclass
class MockCandidate:
    """테스트용 후보 객체 (ThemeCandidate와 같은 속성)"""
    stock_name: str = "테스트"
    stock_code: str = "000000"
    seed_score: int = 50
    corpus_docs: int = 5
    market_rows: int = 10
    source_coverage: int = 2
    target_hits: int = 1
    news_docs: int = 2
    forum_docs: int = 1
    dart_docs: int = 1


def _make_candidates(scores: list[int], corpus_docs: int = 5, market_rows: int = 10) -> list:
    """seed_score 리스트로 후보 생성"""
    return [
        MockCandidate(
            stock_name=f"종목{i}",
            stock_code=f"{i:06d}",
            seed_score=s,
            corpus_docs=corpus_docs,
            market_rows=market_rows,
        )
        for i, s in enumerate(scores)
    ]


class TestBasicClassification:
    """기본 등급 분류"""

    def test_full_analysis(self):
        """seed_score >= 70 → FULL"""
        candidates = _make_candidates([80, 75, 70])
        result = CandidateFilter.classify(candidates)
        assert len(result.full_analysis) == 3
        assert len(result.quick_analysis) == 0
        assert len(result.watch_only) == 0

    def test_quick_analysis(self):
        """40 <= seed_score < 70 → QUICK"""
        candidates = _make_candidates([60, 50, 40])
        result = CandidateFilter.classify(candidates)
        assert len(result.full_analysis) == 0
        assert len(result.quick_analysis) == 3
        assert len(result.watch_only) == 0

    def test_watch_only(self):
        """seed_score < 40 → WATCH"""
        candidates = _make_candidates([30, 20, 10])
        result = CandidateFilter.classify(candidates)
        assert len(result.full_analysis) == 0
        assert len(result.quick_analysis) == 0
        assert len(result.watch_only) == 3

    def test_mixed(self):
        """혼합 등급"""
        candidates = _make_candidates([85, 60, 55, 35, 20])
        result = CandidateFilter.classify(candidates)
        assert len(result.full_analysis) == 1
        assert len(result.quick_analysis) == 2
        assert len(result.watch_only) == 2


class TestBoundaryValues:
    """경계값 테스트"""

    def test_exact_full_threshold(self):
        """seed_score == 70 → FULL"""
        candidates = _make_candidates([70])
        result = CandidateFilter.classify(candidates)
        assert len(result.full_analysis) == 1

    def test_below_full_threshold(self):
        """seed_score == 69 → QUICK"""
        candidates = _make_candidates([69])
        result = CandidateFilter.classify(candidates)
        assert len(result.quick_analysis) == 1

    def test_exact_quick_threshold(self):
        """seed_score == 40 → QUICK"""
        candidates = _make_candidates([40])
        result = CandidateFilter.classify(candidates)
        assert len(result.quick_analysis) == 1

    def test_below_quick_threshold(self):
        """seed_score == 39 → WATCH"""
        candidates = _make_candidates([39])
        result = CandidateFilter.classify(candidates)
        assert len(result.watch_only) == 1

    def test_zero_score(self):
        """seed_score == 0 → WATCH"""
        candidates = _make_candidates([0])
        result = CandidateFilter.classify(candidates)
        assert len(result.watch_only) == 1


class TestDataInsufficiency:
    """데이터 부족 후보 처리"""

    def test_no_data_watch_only(self):
        """corpus_docs=0 AND market_rows=0 → WATCH_ONLY (score 무관)"""
        candidates = _make_candidates([90], corpus_docs=0, market_rows=0)
        result = CandidateFilter.classify(candidates)
        assert len(result.watch_only) == 1
        assert len(result.full_analysis) == 0
        assert "데이터 부족" in result.filter_reasons["000000"]

    def test_corpus_only(self):
        """corpus_docs > 0, market_rows = 0 → 정상 분류"""
        candidates = _make_candidates([80], corpus_docs=5, market_rows=0)
        result = CandidateFilter.classify(candidates)
        assert len(result.full_analysis) == 1

    def test_market_only(self):
        """corpus_docs = 0, market_rows > 0 → 정상 분류"""
        candidates = _make_candidates([80], corpus_docs=0, market_rows=5)
        result = CandidateFilter.classify(candidates)
        assert len(result.full_analysis) == 1


class TestAllCandidatesPreserved:
    """모든 후보가 결과에 포함되는지"""

    def test_total_count_matches(self):
        """입력 후보 수 == full + quick + watch 합계"""
        candidates = _make_candidates([90, 80, 60, 50, 30, 20, 10])
        result = CandidateFilter.classify(candidates)
        total = (
            len(result.full_analysis)
            + len(result.quick_analysis)
            + len(result.watch_only)
        )
        assert total == len(candidates)

    def test_filter_summary_before(self):
        """filter_summary.before == 전체 후보 수"""
        candidates = _make_candidates([90, 60, 30])
        result = CandidateFilter.classify(candidates)
        assert result.filter_summary["before"] == 3

    def test_all_have_reasons(self):
        """모든 후보에 분류 사유가 있는지"""
        candidates = _make_candidates([85, 55, 25])
        result = CandidateFilter.classify(candidates)
        for c in candidates:
            assert c.stock_code in result.filter_reasons


class TestQuickCandidateLimit:
    """quick_candidate_limit 동작"""

    def test_limit_exceeded(self):
        """quick 한도 초과 → WATCH_ONLY"""
        # 모두 QUICK 범위(40~69) 점수
        candidates = _make_candidates([65, 60, 55, 50, 45])
        config = CandidateFilterConfig(quick_candidate_limit=3)
        result = CandidateFilter.classify(candidates, config)
        assert len(result.quick_analysis) == 3
        assert len(result.watch_only) == 2
        # 넘친 후보의 사유에 "초과" 포함
        for c in result.watch_only:
            assert "초과" in result.filter_reasons[c.stock_code]


class TestCustomConfig:
    """사용자 정의 config"""

    def test_lower_thresholds(self):
        """threshold 낮추면 더 많은 후보가 상위 등급"""
        candidates = _make_candidates([50, 30, 15])
        config = CandidateFilterConfig(full_threshold=50, quick_threshold=20)
        result = CandidateFilter.classify(candidates, config)
        assert len(result.full_analysis) == 1   # score=50
        assert len(result.quick_analysis) == 1  # score=30
        assert len(result.watch_only) == 1      # score=15

    def test_min_corpus_docs(self):
        """min_corpus_docs 미달 → WATCH_ONLY"""
        c = MockCandidate(stock_code="001234", seed_score=80, corpus_docs=1, market_rows=5)
        config = CandidateFilterConfig(min_corpus_docs=3)
        result = CandidateFilter.classify([c], config)
        assert len(result.watch_only) == 1


class TestEmptyCandidates:
    """빈 후보 처리"""

    def test_empty_list(self):
        result = CandidateFilter.classify([])
        assert result.filter_summary["before"] == 0
        assert len(result.full_analysis) == 0
        assert len(result.quick_analysis) == 0
        assert len(result.watch_only) == 0


class TestBuildTierEntry:
    """build_tier_entry 딕셔너리 생성"""

    def test_full_tier(self):
        c = MockCandidate(stock_name="삼성전자", stock_code="005930", seed_score=85)
        entry = CandidateFilter.build_tier_entry(c, TIER_FULL, "score 85 >= 70")
        assert entry["tier"] == TIER_FULL
        assert entry["stock_code"] == "005930"
        assert "promoted" not in entry

    def test_quick_tier_not_promoted(self):
        c = MockCandidate(stock_code="000660", seed_score=55)
        entry = CandidateFilter.build_tier_entry(
            c, TIER_QUICK, "score 55", quick_score=62
        )
        assert entry["tier"] == TIER_QUICK
        assert entry["quick_score"] == 62
        assert entry["promoted"] is False

    def test_quick_tier_promoted(self):
        c = MockCandidate(stock_code="000660", seed_score=55)
        entry = CandidateFilter.build_tier_entry(
            c, TIER_QUICK, "score 55",
            quick_score=78, promoted=True,
            promoted_reason="quick_score 78 >= promote_threshold 70",
        )
        assert entry["promoted"] is True
        assert "78" in entry["promoted_reason"]
