from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from src.agents.analyst import AnalystAgent
from src.agents.chartist import ChartistAgent, ChartistScore
from src.agents.context import AgentContextPacket, EvidenceItem
from src.agents.llm_config import get_instruct_llm
from src.agents.quant import QuantScore
from src.agents.risk_manager import AgentScores, FinalDecision, RiskManagerAgent
from src.config.settings import get_data_dir
from src.ingestion.theme_targets import make_theme_key
from src.utils.parallel import is_error, run_agents_parallel

logger = logging.getLogger(__name__)


@dataclass
class ThemeCandidate:
    stock_name: str
    stock_code: str
    target_hits: int = 0
    corpus_docs: int = 0
    news_docs: int = 0
    forum_docs: int = 0
    dart_docs: int = 0
    market_rows: int = 0
    source_coverage: int = 0
    seed_score: int = 0


class ThemeAnalystEvaluation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    moat_score: int = 20
    growth_score: int = 15
    grade: str = "C"
    summary: str = ""
    key_points: List[str] = Field(default_factory=list)
    catalysts: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    contrarian_view: str = ""


class ThemeQuantEvaluation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    valuation_score: int = 12
    profitability_score: int = 12
    growth_score: int = 12
    stability_score: int = 12
    valuation_analysis: str = ""
    profitability_analysis: str = ""
    growth_analysis: str = ""
    stability_analysis: str = ""
    opinion: str = ""
    per: Optional[float] = None
    pbr: Optional[float] = None
    roe: Optional[float] = None
    debt_ratio: Optional[float] = None


class ThemeLeaderOrchestrator:
    """
    Theme-level stock leadership orchestration.

    목적:
    - theme_targets / corpus / market_data를 읽어 후보군 자동 추출
    - 후보별 Analyst / Quant / Chartist를 데이터 기반으로 평가
    - RiskManager가 최종 순위를 종합
    """

    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = Path(data_dir) if data_dir else get_data_dir()
        self.analyst = AnalystAgent()
        self.chartist = ChartistAgent()
        self.risk_manager = RiskManagerAgent()
        self.instruct_llm = get_instruct_llm()

    def run(
        self,
        theme: str,
        theme_key: str = "",
        candidate_limit: int = 5,
        top_n: int = 3,
    ) -> Dict[str, Any]:
        resolved_key = theme_key or make_theme_key(theme, theme)
        candidates = self.extract_candidates(
            theme=theme,
            theme_key=resolved_key,
            candidate_limit=candidate_limit,
        )
        if not candidates:
            return {
                "status": "error",
                "message": f"테마 후보군을 찾지 못했습니다: {theme}",
                "theme": theme,
                "theme_key": resolved_key,
            }

        evaluations = []
        for candidate in candidates:
            evaluations.append(self.evaluate_candidate(theme, resolved_key, candidate))

        evaluations.sort(
            key=lambda row: (
                row.get("leader_score", 0),
                row.get("final_decision", {}).get("confidence", 0),
                row.get("candidate", {}).get("seed_score", 0),
            ),
            reverse=True,
        )
        leaders = evaluations[:top_n]

        summary_lines = [
            f"{idx}. {row['candidate']['stock_name']}({row['candidate']['stock_code']})"
            f" - leader_score {row['leader_score']}, "
            f"{row['final_decision'].get('action', 'N/A')}, "
            f"확신도 {row['final_decision'].get('confidence', 0)}%"
            for idx, row in enumerate(leaders, start=1)
        ]

        return {
            "status": "success",
            "theme": theme,
            "theme_key": resolved_key,
            "candidate_count": len(candidates),
            "evaluated_count": len(evaluations),
            "leaders": leaders,
            "summary": "\n".join(summary_lines),
        }

    def extract_candidates(
        self,
        theme: str,
        theme_key: str,
        candidate_limit: int = 5,
    ) -> List[ThemeCandidate]:
        target_counter, target_names = self._load_theme_targets(theme_key)
        corpus_stats = self._load_corpus_stats(theme_key)
        market_counts = self._load_market_counts(theme_key)

        candidate_codes = set(target_counter) | set(corpus_stats) | set(market_counts)
        candidates: List[ThemeCandidate] = []

        for code in candidate_codes:
            name = (
                target_names.get(code)
                or corpus_stats.get(code, {}).get("stock_name")
                or f"UNKNOWN-{code}"
            )
            stats = corpus_stats.get(code, {})
            news_docs = stats.get("source_counts", {}).get("news", 0)
            forum_docs = stats.get("source_counts", {}).get("forum", 0)
            dart_docs = stats.get("source_counts", {}).get("dart", 0)
            corpus_docs = stats.get("doc_count", 0)
            market_rows = market_counts.get(code, 0)
            source_coverage = len([v for v in [news_docs, forum_docs, dart_docs] if v > 0])
            if corpus_docs <= 0 and market_rows <= 0:
                # 로컬 텍스트/차트 근거가 전혀 없는 후보는 주도주 평가 대상에서 제외한다.
                continue
            seed_score = min(
                100,
                target_counter.get(code, 0) * 2
                + min(corpus_docs * 2, 40)
                + source_coverage * 8
                + min(dart_docs * 4, 20)
                + min(news_docs * 2, 15)
                + (10 if market_rows >= 20 else 5 if market_rows > 0 else 0),
            )
            candidates.append(
                ThemeCandidate(
                    stock_name=name,
                    stock_code=code,
                    target_hits=target_counter.get(code, 0),
                    corpus_docs=corpus_docs,
                    news_docs=news_docs,
                    forum_docs=forum_docs,
                    dart_docs=dart_docs,
                    market_rows=market_rows,
                    source_coverage=source_coverage,
                    seed_score=seed_score,
                )
            )

        candidates.sort(
            key=lambda row: (
                row.seed_score,
                row.corpus_docs,
                row.dart_docs,
                row.news_docs,
            ),
            reverse=True,
        )
        logger.info(
            "[ThemeOrchestrator] theme=%s key=%s candidates=%s",
            theme,
            theme_key,
            [(c.stock_name, c.stock_code, c.seed_score) for c in candidates[:candidate_limit]],
        )
        return candidates[:candidate_limit]

    def evaluate_candidate(
        self,
        theme: str,
        theme_key: str,
        candidate: ThemeCandidate,
    ) -> Dict[str, Any]:
        records = self._load_stock_records(theme_key, candidate.stock_code)
        source_counts = Counter((row.get("metadata") or {}).get("source_type", "") for row in records)
        analyst_context = self._compose_context(records, {"news", "forum", "dart"}, max_docs=6)
        quant_context = self._compose_context(records, {"dart", "news"}, max_docs=5)

        tasks = {
            "analyst": (
                self._evaluate_analyst_candidate,
                (theme, candidate, analyst_context, dict(source_counts)),
            ),
            "quant": (
                self._evaluate_quant_candidate,
                (theme, candidate, quant_context, dict(source_counts)),
            ),
            "chartist": (
                self._evaluate_chartist_candidate,
                (theme_key, candidate),
            ),
        }
        results = run_agents_parallel(tasks, max_workers=3, timeout=240)

        analyst_result = results.get("analyst")
        quant_result = results.get("quant")
        chartist_result = results.get("chartist")

        if is_error(analyst_result):
            analyst_result = self._fallback_analyst(theme, candidate, analyst_context, dict(source_counts))
        if is_error(quant_result):
            quant_result = self._fallback_quant(theme, candidate, quant_context, dict(source_counts))
        if is_error(chartist_result) or not isinstance(chartist_result, ChartistScore):
            chartist_result = self.chartist._default_score(candidate.stock_code, "테마 차트 분석 실패")

        scores = AgentScores(
            analyst_moat_score=analyst_result["moat_score"],
            analyst_growth_score=analyst_result["growth_score"],
            analyst_total=analyst_result["total_score"],
            analyst_grade=analyst_result["grade"],
            analyst_opinion=analyst_result["summary"],
            quant_valuation_score=quant_result.valuation_score,
            quant_profitability_score=quant_result.profitability_score,
            quant_growth_score=quant_result.growth_score,
            quant_stability_score=quant_result.stability_score,
            quant_total=quant_result.total_score,
            quant_opinion=quant_result.opinion,
            chartist_trend_score=chartist_result.trend_score,
            chartist_momentum_score=chartist_result.momentum_score,
            chartist_volatility_score=chartist_result.volatility_score,
            chartist_volume_score=chartist_result.volume_score,
            chartist_total=chartist_result.total_score,
            chartist_signal=chartist_result.signal,
            analyst_context=analyst_result["packet"].to_dict(),
            quant_context=quant_result.analysis_packet,
            chartist_context=chartist_result.analysis_packet,
        )

        final_decision = self.risk_manager.make_decision(
            candidate.stock_name,
            candidate.stock_code,
            scores,
        )
        data_presence_score = min(100, candidate.seed_score)
        leader_score = round(final_decision.total_score * 0.7 + data_presence_score * 0.3)

        return {
            "candidate": {
                "stock_name": candidate.stock_name,
                "stock_code": candidate.stock_code,
                "seed_score": candidate.seed_score,
                "target_hits": candidate.target_hits,
                "corpus_docs": candidate.corpus_docs,
                "news_docs": candidate.news_docs,
                "forum_docs": candidate.forum_docs,
                "dart_docs": candidate.dart_docs,
                "market_rows": candidate.market_rows,
            },
            "leader_score": leader_score,
            "data_presence_score": data_presence_score,
            "analyst": {
                "total_score": analyst_result["total_score"],
                "grade": analyst_result["grade"],
                "summary": analyst_result["summary"],
                "catalysts": analyst_result["packet"].catalysts,
                "risks": analyst_result["packet"].risks,
            },
            "quant": {
                "total_score": quant_result.total_score,
                "grade": quant_result.grade,
                "opinion": quant_result.opinion,
            },
            "chartist": {
                "total_score": chartist_result.total_score,
                "signal": chartist_result.signal,
                "short_term_opinion": chartist_result.short_term_opinion,
                "mid_term_opinion": chartist_result.mid_term_opinion,
            },
            "final_decision": self._decision_to_dict(final_decision),
        }

    def _evaluate_analyst_candidate(
        self,
        theme: str,
        candidate: ThemeCandidate,
        context: str,
        source_counts: Dict[str, int],
    ) -> Dict[str, Any]:
        if not context.strip():
            return self._fallback_analyst(theme, candidate, context, source_counts)

        prompt = f"""
당신은 테마 주도주를 선별하는 AnalystAgent입니다.
아래 로컬 데이터만 보고 '{theme}' 테마에서 '{candidate.stock_name}'({candidate.stock_code})의
주도주 가능성을 평가하세요.

[데이터 제약]
- 외부 지식 금지
- 아래 컨텍스트만 근거로 판단
- JSON만 출력

[후보 메타]
- target_hits: {candidate.target_hits}
- corpus_docs: {candidate.corpus_docs}
- source_counts: {json.dumps(source_counts, ensure_ascii=False)}

[컨텍스트]
{context[:4200]}

다음 JSON 형식으로만 응답하세요:
{{
  "moat_score": 0,
  "growth_score": 0,
  "grade": "C",
  "summary": "",
  "key_points": ["", ""],
  "catalysts": ["", ""],
  "risks": ["", ""],
  "contrarian_view": ""
}}
"""
        data = self._invoke_json(
            self.analyst.thinking_llm,
            prompt,
            ThemeAnalystEvaluation,
            label=f"analyst:{candidate.stock_name}",
        )
        if not data:
            return self._fallback_analyst(theme, candidate, context, source_counts)

        moat = max(0, min(40, int(data.get("moat_score", 20))))
        growth = max(0, min(30, int(data.get("growth_score", 15))))
        total = moat + growth
        packet = AgentContextPacket(
            agent_name="analyst",
            stock_name=candidate.stock_name,
            stock_code=candidate.stock_code,
            summary=data.get("summary", ""),
            key_points=[item for item in data.get("key_points", []) if str(item).strip()][:4],
            catalysts=[item for item in data.get("catalysts", []) if str(item).strip()][:4],
            risks=[item for item in data.get("risks", []) if str(item).strip()][:4],
            contrarian_view=data.get("contrarian_view", ""),
            evidence=self._make_evidence(context, limit=3),
            score=total,
            confidence=min(95, 40 + candidate.seed_score // 2),
            grade=str(data.get("grade", self._score_to_grade(total, 70))),
            signal=data.get("summary", ""),
            next_action="risk_manager_review",
            source_tags=sorted(source_counts.keys()),
        )
        return {
            "moat_score": moat,
            "growth_score": growth,
            "total_score": total,
            "grade": packet.grade,
            "summary": packet.summary,
            "packet": packet,
        }

    def _evaluate_quant_candidate(
        self,
        theme: str,
        candidate: ThemeCandidate,
        context: str,
        source_counts: Dict[str, int],
    ) -> QuantScore:
        if not context.strip():
            return self._fallback_quant(theme, candidate, context, source_counts)

        prompt = f"""
당신은 QuantAgent입니다.
아래 '{theme}' 테마 로컬 데이터만 사용하여 '{candidate.stock_name}'({candidate.stock_code})의
재무/기초체력 관점 점수를 평가하세요.

[데이터 제약]
- 외부 지식 금지
- DART/뉴스 컨텍스트만 근거 사용
- 숫자가 부족하면 보수적으로 중립 점수를 부여
- JSON만 출력

[컨텍스트]
{context[:4200]}

다음 JSON 형식으로만 응답하세요:
{{
  "valuation_score": 0,
  "profitability_score": 0,
  "growth_score": 0,
  "stability_score": 0,
  "valuation_analysis": "",
  "profitability_analysis": "",
  "growth_analysis": "",
  "stability_analysis": "",
  "opinion": "",
  "per": null,
  "pbr": null,
  "roe": null,
  "debt_ratio": null
}}
"""
        data = self._invoke_json(
            self.instruct_llm,
            prompt,
            ThemeQuantEvaluation,
            label=f"quant:{candidate.stock_name}",
        )
        if not data:
            return self._fallback_quant(theme, candidate, context, source_counts)

        valuation = max(0, min(25, int(data.get("valuation_score", 12))))
        profitability = max(0, min(25, int(data.get("profitability_score", 12))))
        growth = max(0, min(25, int(data.get("growth_score", 12))))
        stability = max(0, min(25, int(data.get("stability_score", 12))))
        total = valuation + profitability + growth + stability
        packet = AgentContextPacket(
            agent_name="quant",
            stock_name=candidate.stock_name,
            stock_code=candidate.stock_code,
            summary=data.get("opinion", ""),
            key_points=[
                data.get("valuation_analysis", ""),
                data.get("profitability_analysis", ""),
                data.get("growth_analysis", ""),
                data.get("stability_analysis", ""),
            ],
            risks=self._keyword_risks(context),
            catalysts=self._keyword_catalysts(context),
            contrarian_view="테마 로컬 DART/뉴스만 사용한 정성 기반 정량화",
            evidence=self._make_evidence(context, limit=3),
            score=total,
            confidence=min(90, 35 + candidate.dart_docs * 10 + candidate.news_docs * 2),
            grade=self._score_to_grade(total, 100),
            signal=data.get("opinion", ""),
            next_action="risk_manager_review",
            source_tags=sorted(source_counts.keys()),
        )
        return QuantScore(
            valuation_score=valuation,
            profitability_score=profitability,
            growth_score=growth,
            stability_score=stability,
            total_score=total,
            valuation_analysis=data.get("valuation_analysis", ""),
            profitability_analysis=data.get("profitability_analysis", ""),
            growth_analysis=data.get("growth_analysis", ""),
            stability_analysis=data.get("stability_analysis", ""),
            per=data.get("per"),
            pbr=data.get("pbr"),
            roe=data.get("roe"),
            debt_ratio=data.get("debt_ratio"),
            opinion=data.get("opinion", ""),
            grade=self._score_to_grade(total, 100),
            analysis_packet=packet.to_dict(),
        )

    def _evaluate_chartist_candidate(
        self,
        theme_key: str,
        candidate: ThemeCandidate,
    ) -> ChartistScore:
        # 현재 ChartistAgent는 로컬 market_data/raw chart를 우선 사용한다.
        return self.chartist.full_analysis(candidate.stock_name, candidate.stock_code)

    def _fallback_analyst(
        self,
        theme: str,
        candidate: ThemeCandidate,
        context: str,
        source_counts: Dict[str, int],
    ) -> Dict[str, Any]:
        moat = min(40, candidate.dart_docs * 6 + candidate.news_docs * 2 + candidate.source_coverage * 4)
        growth = min(30, candidate.news_docs * 2 + candidate.forum_docs + max(0, candidate.target_hits // 2))
        total = moat + growth
        summary = (
            f"{candidate.stock_name}은(는) '{theme}' 데이터에서 "
            f"문서 {candidate.corpus_docs}건, 출처 {candidate.source_coverage}종으로 포착된 후보입니다."
        )
        packet = AgentContextPacket(
            agent_name="analyst",
            stock_name=candidate.stock_name,
            stock_code=candidate.stock_code,
            summary=summary,
            key_points=[
                f"DART {candidate.dart_docs}건 / 뉴스 {candidate.news_docs}건 / 게시판 {candidate.forum_docs}건",
                f"theme_targets 등장 횟수 {candidate.target_hits}회",
            ],
            catalysts=self._keyword_catalysts(context),
            risks=self._keyword_risks(context),
            contrarian_view="LLM JSON 평가 실패로 데이터 빈도 기반 휴리스틱 사용",
            evidence=self._make_evidence(context, limit=3),
            score=total,
            confidence=min(80, 30 + candidate.seed_score // 2),
            grade=self._score_to_grade(total, 70),
            signal=summary,
            next_action="risk_manager_review",
            source_tags=sorted(source_counts.keys()),
        )
        return {
            "moat_score": moat,
            "growth_score": growth,
            "total_score": total,
            "grade": packet.grade,
            "summary": packet.summary,
            "packet": packet,
        }

    def _fallback_quant(
        self,
        theme: str,
        candidate: ThemeCandidate,
        context: str,
        source_counts: Dict[str, int],
    ) -> QuantScore:
        bad_keywords = len(re.findall(r"적자|감소|하락|부진|리스크|악화", context))
        good_keywords = len(re.findall(r"증가|개선|성장|수혜|확대|매수", context))
        valuation = max(5, min(25, 12 + good_keywords - bad_keywords))
        profitability = max(5, min(25, 10 + candidate.dart_docs * 3 - bad_keywords))
        growth = max(5, min(25, 10 + good_keywords * 2))
        stability = max(5, min(25, 12 + candidate.dart_docs * 2 - bad_keywords))
        total = valuation + profitability + growth + stability
        summary = (
            f"{candidate.stock_name}의 로컬 DART/뉴스 기반 정량 추정 점수는 {total}/100점입니다."
        )
        packet = AgentContextPacket(
            agent_name="quant",
            stock_name=candidate.stock_name,
            stock_code=candidate.stock_code,
            summary=summary,
            key_points=[
                f"호재 키워드 {good_keywords}회 / 악재 키워드 {bad_keywords}회",
                f"DART 문서 {candidate.dart_docs}건",
            ],
            risks=self._keyword_risks(context),
            catalysts=self._keyword_catalysts(context),
            contrarian_view="수치 추출 대신 텍스트 신호 기반 휴리스틱",
            evidence=self._make_evidence(context, limit=3),
            score=total,
            confidence=min(75, 25 + candidate.dart_docs * 10),
            grade=self._score_to_grade(total, 100),
            signal=summary,
            next_action="risk_manager_review",
            source_tags=sorted(source_counts.keys()),
        )
        return QuantScore(
            valuation_score=valuation,
            profitability_score=profitability,
            growth_score=growth,
            stability_score=stability,
            total_score=total,
            valuation_analysis="로컬 DART/뉴스 텍스트 기준 밸류에이션 추정",
            profitability_analysis="로컬 DART 텍스트 기준 수익성 추정",
            growth_analysis="로컬 뉴스/포럼 모멘텀 기준 성장성 추정",
            stability_analysis="공시 및 악재 키워드 기준 안정성 추정",
            opinion=summary,
            grade=self._score_to_grade(total, 100),
            analysis_packet=packet.to_dict(),
        )

    def _decision_to_dict(self, decision: FinalDecision) -> Dict[str, Any]:
        return {
            "total_score": decision.total_score,
            "action": decision.action.value,
            "confidence": decision.confidence,
            "risk_level": decision.risk_level.value,
            "summary": decision.summary,
            "key_catalysts": decision.key_catalysts,
            "risk_factors": decision.risk_factors,
            "detailed_reasoning": decision.detailed_reasoning,
        }

    def _load_theme_targets(self, theme_key: str) -> Tuple[Counter, Dict[str, str]]:
        path = self.data_dir / "raw" / "theme_targets" / f"{theme_key}.jsonl"
        counter: Counter = Counter()
        names: Dict[str, str] = {}
        if not path.exists():
            return counter, names
        for row in self._iter_jsonl(path):
            code = str(row.get("stock_code", "")).strip()
            name = str(row.get("stock_name", "")).strip()
            if not code or not name:
                continue
            counter[code] += 1
            names[code] = name
        return counter, names

    def _load_corpus_stats(self, theme_key: str) -> Dict[str, Dict[str, Any]]:
        path = self.data_dir / "canonical_index" / theme_key / "corpus.jsonl"
        stats: Dict[str, Dict[str, Any]] = {}
        if not path.exists():
            return stats
        for row in self._iter_jsonl(path):
            meta = row.get("metadata") or {}
            code = str(meta.get("stock_code", "")).strip()
            name = str(meta.get("stock_name", "")).strip()
            if not code or not name:
                continue
            bucket = stats.setdefault(
                code,
                {
                    "stock_name": name,
                    "doc_count": 0,
                    "source_counts": Counter(),
                },
            )
            bucket["doc_count"] += 1
            bucket["source_counts"][meta.get("source_type", "")] += 1
        return stats

    def _load_stock_records(self, theme_key: str, stock_code: str) -> List[Dict[str, Any]]:
        path = self.data_dir / "canonical_index" / theme_key / "corpus.jsonl"
        records: List[Dict[str, Any]] = []
        if not path.exists():
            return records
        for row in self._iter_jsonl(path):
            meta = row.get("metadata") or {}
            if str(meta.get("stock_code", "")).strip() != stock_code:
                continue
            records.append(row)
        records.sort(
            key=lambda row: (
                (row.get("metadata") or {}).get("published_at", ""),
                (row.get("metadata") or {}).get("credibility_score", 0),
            ),
            reverse=True,
        )
        return records

    def _load_market_counts(self, theme_key: str) -> Counter:
        counter: Counter = Counter()
        candidates = [
            self.data_dir / "market_data" / theme_key / "chart.jsonl",
            self.data_dir / "market_data" / theme_key / "combined.jsonl",
            self.data_dir / "raw" / "chart" / f"{theme_key}.jsonl",
        ]
        for path in candidates:
            if not path.exists():
                continue
            for row in self._iter_jsonl(path):
                code = str(row.get("stock_code", "")).strip()
                if code:
                    counter[code] += 1
        return counter

    def _compose_context(
        self,
        records: List[Dict[str, Any]],
        allowed_sources: set[str],
        max_docs: int = 5,
        max_chars: int = 4200,
    ) -> str:
        lines: List[str] = []
        total = 0
        picked = 0
        for row in records:
            meta = row.get("metadata") or {}
            source = str(meta.get("source_type", "")).strip()
            if source not in allowed_sources:
                continue
            title = str(meta.get("title", "")).strip() or "(untitled)"
            published_at = str(meta.get("published_at", "")).strip()
            text = str(row.get("text", "")).strip()
            if not text:
                continue
            block = (
                f"[source={source} title={title} date={published_at}]\n"
                f"{text[:700].strip()}\n"
            )
            lines.append(block)
            total += len(block)
            picked += 1
            if picked >= max_docs or total >= max_chars:
                break
        return "\n".join(lines)

    def _make_evidence(self, context: str, limit: int = 3) -> List[EvidenceItem]:
        evidence: List[EvidenceItem] = []
        pattern = re.compile(r"\[source=(?P<source>[^\s]+) title=(?P<title>.*?) date=(?P<date>[^\]]*)\]\n(?P<text>.*)")
        for block in context.split("\n\n"):
            block = block.strip()
            if not block:
                continue
            match = pattern.match(block)
            if not match:
                continue
            evidence.append(
                EvidenceItem(
                    source=match.group("source"),
                    title=match.group("title"),
                    snippet=match.group("text")[:220],
                    note=match.group("date"),
                )
            )
            if len(evidence) >= limit:
                break
        return evidence

    def _keyword_catalysts(self, context: str) -> List[str]:
        patterns = [
            ("성장/개선", r"증가|개선|성장|확대|회복"),
            ("수요/모멘텀", r"수요|모멘텀|휴머노이드|ESS|전기차"),
            ("투자의견", r"매수|목표주가|긍정"),
        ]
        catalysts: List[str] = []
        for label, pattern in patterns:
            if re.search(pattern, context):
                catalysts.append(label)
        return catalysts[:4]

    def _keyword_risks(self, context: str) -> List[str]:
        patterns = [
            ("실적 부진", r"적자|감소|부진|악화"),
            ("원가/원재료", r"원재료|유가|원가"),
            ("변동성", r"하락|급락|리스크|우려"),
        ]
        risks: List[str] = []
        for label, pattern in patterns:
            if re.search(pattern, context):
                risks.append(label)
        return risks[:4]

    def _score_to_grade(self, score: int, scale: int) -> str:
        ratio = score / max(scale, 1)
        if ratio >= 0.8:
            return "A"
        if ratio >= 0.65:
            return "B"
        if ratio >= 0.5:
            return "C"
        if ratio >= 0.35:
            return "D"
        return "F"

    def _invoke_json(
        self,
        llm: Any,
        prompt: str,
        schema: type[BaseModel],
        *,
        label: str = "theme_orchestrator",
    ) -> Dict[str, Any]:
        try:
            structured_llm = self._build_structured_llm(llm, schema)
            if structured_llm is not None:
                structured = structured_llm.invoke(prompt)
                return self._validate_structured_payload(structured, schema)
        except Exception as exc:
            logger.warning(
                "Theme orchestration structured output failed (%s): %s",
                label,
                exc,
            )

        try:
            response = llm.invoke(prompt)
            content = self._response_to_text(getattr(response, "content", response))
            payload = self._extract_first_json_object(content)
            if not payload:
                if content.lstrip().startswith("[mock:"):
                    logger.debug(
                        "Theme orchestration mock response did not include JSON (%s)",
                        label,
                    )
                    return {}
                logger.warning(
                    "Theme orchestration JSON payload missing (%s): %.200s",
                    label,
                    content,
                )
                return {}
            return self._validate_structured_payload(payload, schema)
        except Exception as exc:
            logger.warning("Theme orchestration JSON parse failed (%s): %s", label, exc)
            return {}

    @staticmethod
    def _build_structured_llm(llm: Any, schema: type[BaseModel]):
        if not hasattr(llm, "with_structured_output"):
            return None
        try:
            # Ollama는 json_schema/json_mode를 지원하므로 우선 구조화 응답을 강제한다.
            return llm.with_structured_output(schema, method="json_schema")
        except Exception:
            return llm.with_structured_output(schema, method="json_mode")

    @staticmethod
    def _validate_structured_payload(payload: Any, schema: type[BaseModel]) -> Dict[str, Any]:
        if isinstance(payload, BaseModel):
            return payload.model_dump()
        if not isinstance(payload, dict):
            raise TypeError(f"Expected dict payload for {schema.__name__}, got {type(payload).__name__}")
        return schema.model_validate(payload).model_dump()

    @staticmethod
    def _response_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: List[str] = []
            for item in content:
                if isinstance(item, str):
                    chunks.append(item)
                elif isinstance(item, dict):
                    text = item.get("text") or item.get("content") or ""
                    if text:
                        chunks.append(str(text))
                else:
                    chunks.append(str(item))
            return "\n".join(chunk for chunk in chunks if chunk)
        return str(content)

    @staticmethod
    def _extract_first_json_object(text: str) -> Dict[str, Any]:
        if not text:
            return {}

        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        decoder = json.JSONDecoder()
        for index, char in enumerate(cleaned):
            if char != "{":
                continue
            try:
                payload, _end = decoder.raw_decode(cleaned[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        return {}

    @staticmethod
    def _iter_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)
