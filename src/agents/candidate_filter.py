# 파일: src/agents/candidate_filter.py
"""
후보 1차 필터링 (CandidateFilter)

후보를 seed_score 기반으로 3등급으로 분류합니다:
- FULL_ANALYSIS: Analyst + Quant + Chartist + RiskManager (정밀 분석)
- QUICK_ANALYSIS: Quant + Chartist 중심 휴리스틱 (간이 분석)
- WATCH_ONLY: 결과에 남기지만 정밀 분석 생략

핵심 원칙:
- 후보를 삭제하지 않음 (WATCH_ONLY도 candidate_tiers에 남김)
- threshold는 CandidateFilterConfig로 외부에서 조정 가능
- QUICK 후보 중 성적이 좋으면 FULL로 승격 가능

사용 예시:
    from src.agents.candidate_filter import CandidateFilter, CandidateFilterConfig

    config = CandidateFilterConfig(full_threshold=70, quick_threshold=40)
    result = CandidateFilter.classify(candidates, config)

    # result.full_analysis → 정밀 분석 대상
    # result.quick_analysis → 간이 분석 대상
    # result.watch_only → 관찰만
    # result.filter_summary → {"before": 24, "full": 6, "quick": 5, "watch": 13}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from src.agents.theme_orchestrator import ThemeCandidate

# 후보 등급 상수
TIER_FULL = "FULL_ANALYSIS"
TIER_QUICK = "QUICK_ANALYSIS"
TIER_WATCH = "WATCH_ONLY"


@dataclass
class CandidateFilterConfig:
    """
    후보 필터링 설정

    threshold 값을 조정하여 필터링 강도를 제어합니다.
    """
    # FULL_ANALYSIS 기준 seed_score
    full_threshold: int = 70
    # QUICK_ANALYSIS 기준 seed_score (이 미만은 WATCH_ONLY)
    quick_threshold: int = 40
    # QUICK 평가 최대 수 (비용 제어)
    quick_candidate_limit: int = 10
    # QUICK → FULL 승격 기준 (combined_score)
    promote_threshold: int = 70
    # 최소 문서 수 (미달이면 WATCH_ONLY)
    min_corpus_docs: int = 0
    # 최소 출처 다양성 (미달이면 WATCH_ONLY)
    min_source_coverage: int = 0


@dataclass
class CandidateFilterResult:
    """
    후보 필터링 결과

    모든 후보가 3개 리스트 중 하나에 반드시 포함됩니다.
    filter_reasons에 각 후보의 분류 사유가 기록됩니다.
    """
    # 정밀 분석 대상 (Analyst + Quant + Chartist + RiskManager)
    full_analysis: List[Any] = field(default_factory=list)
    # 간이 분석 대상 (Quant + Chartist)
    quick_analysis: List[Any] = field(default_factory=list)
    # 관찰만 (seed_score 기반 기본값)
    watch_only: List[Any] = field(default_factory=list)
    # 분류 사유: {stock_code: "seed_score 35 < quick_threshold 40"}
    filter_reasons: Dict[str, str] = field(default_factory=dict)
    # 요약 통계
    filter_summary: Dict[str, int] = field(default_factory=dict)


class CandidateFilter:
    """
    후보 등급 분류기

    seed_score와 데이터 품질을 기준으로 후보를 3등급으로 나눕니다.
    """

    @staticmethod
    def classify(
        candidates: List[Any],
        config: CandidateFilterConfig | None = None,
    ) -> CandidateFilterResult:
        """
        후보 목록을 등급별로 분류

        분류 순서:
        1. 데이터 부족(corpus_docs=0 AND market_rows=0) → WATCH_ONLY
        2. seed_score >= full_threshold → FULL_ANALYSIS
        3. seed_score >= quick_threshold → QUICK_ANALYSIS (quick_candidate_limit까지)
        4. 나머지 → WATCH_ONLY

        Args:
            candidates: ThemeCandidate 리스트 (seed_score 내림차순 정렬 권장)
            config: 필터링 설정 (None이면 기본값)

        Returns:
            CandidateFilterResult
        """
        if config is None:
            config = CandidateFilterConfig()

        result = CandidateFilterResult()
        quick_count = 0

        for candidate in candidates:
            code = getattr(candidate, "stock_code", "unknown")
            score = getattr(candidate, "seed_score", 0)
            corpus_docs = getattr(candidate, "corpus_docs", 0)
            market_rows = getattr(candidate, "market_rows", 0)
            source_coverage = getattr(candidate, "source_coverage", 0)

            # 데이터 부족 → WATCH_ONLY
            if corpus_docs <= 0 and market_rows <= 0:
                reason = f"데이터 부족 (corpus_docs={corpus_docs}, market_rows={market_rows})"
                result.watch_only.append(candidate)
                result.filter_reasons[code] = reason
                continue

            # 최소 요구사항 미달 → WATCH_ONLY
            if (config.min_corpus_docs > 0 and corpus_docs < config.min_corpus_docs):
                reason = f"corpus_docs {corpus_docs} < min {config.min_corpus_docs}"
                result.watch_only.append(candidate)
                result.filter_reasons[code] = reason
                continue

            if (config.min_source_coverage > 0 and source_coverage < config.min_source_coverage):
                reason = f"source_coverage {source_coverage} < min {config.min_source_coverage}"
                result.watch_only.append(candidate)
                result.filter_reasons[code] = reason
                continue

            # seed_score 기반 등급 판정
            if score >= config.full_threshold:
                result.full_analysis.append(candidate)
                result.filter_reasons[code] = (
                    f"seed_score {score} >= full_threshold {config.full_threshold}"
                )
            elif score >= config.quick_threshold:
                if quick_count < config.quick_candidate_limit:
                    result.quick_analysis.append(candidate)
                    result.filter_reasons[code] = (
                        f"seed_score {score} >= quick_threshold {config.quick_threshold}, "
                        f"< full_threshold {config.full_threshold}"
                    )
                    quick_count += 1
                else:
                    # quick 한도 초과 → WATCH_ONLY
                    result.watch_only.append(candidate)
                    result.filter_reasons[code] = (
                        f"seed_score {score} (QUICK 대상이나 "
                        f"quick_candidate_limit {config.quick_candidate_limit} 초과)"
                    )
            else:
                result.watch_only.append(candidate)
                result.filter_reasons[code] = (
                    f"seed_score {score} < quick_threshold {config.quick_threshold}"
                )

        # 요약 통계
        result.filter_summary = {
            "before": len(candidates),
            "full_analysis": len(result.full_analysis),
            "quick_analysis": len(result.quick_analysis),
            "watch_only": len(result.watch_only),
        }

        return result

    @staticmethod
    def build_tier_entry(
        candidate: Any,
        tier: str,
        reason: str,
        quick_score: int = 0,
        promoted: bool = False,
        promoted_reason: str = "",
    ) -> Dict[str, Any]:
        """
        candidate_tiers 결과 항목 생성

        Args:
            candidate: ThemeCandidate 객체
            tier: 등급 (TIER_FULL / TIER_QUICK / TIER_WATCH)
            reason: 분류 사유
            quick_score: QUICK 분석 점수 (해당 시)
            promoted: FULL로 승격 여부
            promoted_reason: 승격 사유

        Returns:
            결과 딕셔너리 (candidate_tiers 항목)
        """
        entry: Dict[str, Any] = {
            "stock_name": getattr(candidate, "stock_name", ""),
            "stock_code": getattr(candidate, "stock_code", ""),
            "seed_score": getattr(candidate, "seed_score", 0),
            "tier": tier,
            "reason": reason,
        }

        if tier == TIER_QUICK:
            entry["quick_score"] = quick_score
            entry["promoted"] = promoted
            if promoted:
                entry["promoted_reason"] = promoted_reason

        return entry
