# 파일: src/agents/analyst.py
"""
Analyst Agent (애널리스트 에이전트)

역할:
- DART/뉴스/포럼 검색
- 수집된 원시 데이터를 하나의 Thinking 프롬프트에 주입
- 독점력(Moat) + 성장성(Growth) + 최종 헤게모니 등급 도출
"""

import json
import logging
import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

from src.agents.llm_config import get_analyst_llm, get_summary_llm
from src.utils.prompt_loader import load_prompt

# RAG 검색 도구 (Canonical Retriever 통합)
from src.tools.rag_tool import RAGSearchTool

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 데이터 클래스
# ──────────────────────────────────────────────

@dataclass
class ResearchResult:
    """애널리스트 리서치 결과 데이터 클래스"""
    stock_name: str
    stock_code: str

    # DART/뉴스 투자 근거
    evidence_summary: str = ""
    evidence_sources: List[str] = field(default_factory=list)

    # 뉴스/포럼 정보
    news_summary: str = ""

    # 메타데이터
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    # 정보 품질 평가
    data_sources: Dict = field(default_factory=dict)
    quality_score: int = 0
    quality_warnings: List[str] = field(default_factory=list)

    def evaluate_quality(self):
        """정보 품질을 자동 평가하고 경고 생성"""
        score = 0
        self.quality_warnings = []

        empty_indicators = ["없음", "오류", "실패", "확보하지 못함", "미설치"]

        def _has_content(text: str) -> bool:
            if not text or not text.strip():
                return False
            return not any(ind in text for ind in empty_indicators)

        # DART/뉴스 투자 근거 (60점) — 가장 중요
        if _has_content(self.evidence_summary):
            score += 60
        else:
            self.quality_warnings.append("DART/뉴스 투자 근거 부재 — 정성적 분석의 신뢰도가 낮을 수 있음")

        # 뉴스/포럼 심리 (40점)
        if _has_content(self.news_summary):
            score += 40
        else:
            self.quality_warnings.append("뉴스/포럼 정보 부재 — 시장 센티먼트 파악 제한적")

        self.quality_score = score

    @property
    def quality_grade(self) -> str:
        """정보 품질 등급"""
        if self.quality_score >= 80:
            return "A"
        elif self.quality_score >= 60:
            return "B"
        elif self.quality_score >= 40:
            return "C"
        else:
            return "D"

    def to_analysis_prompt(self) -> str:
        """Thinking 모델에 전달할 통합 프롬프트 생성"""
        quality_section = f"""## 0. 정보 품질 평가
- 품질 등급: {self.quality_grade} ({self.quality_score}/100)
- 데이터 소스: {', '.join(f'{k}={v}' for k, v in self.data_sources.items()) if self.data_sources else 'N/A'}"""

        if self.quality_warnings:
            quality_section += "\n- ⚠️ 경고:"
            for w in self.quality_warnings:
                quality_section += f"\n  - {w}"
            quality_section += "\n\n※ 위 경고 사항을 감안하여 분석 신뢰도를 조정해주세요."

        return f"""
# {self.stock_name} ({self.stock_code}) 리서치 요약

{quality_section}

## 1. DART/뉴스 기반 투자 근거
{self.evidence_summary or "DART/뉴스 투자 근거 없음"}

## 2. 최신 뉴스/포럼 심리
{self.news_summary or "뉴스 정보 없음"}

---
리서치 시점: {self.timestamp}
"""


@dataclass
class AnalystScore:
    """애널리스트 분석 점수"""
    moat_score: int        # 독점력 (0-50점)
    growth_score: int      # 성장성 (0-50점)
    total_score: int       # 총점 (0-100점)
    moat_reason: str
    growth_reason: str
    evidence_summary: str
    final_opinion: str

    # 추가 필드
    hegemony_grade: str = "C"
    competitive_advantage: str = ""
    risk_factors: str = ""
    detailed_reasoning: str = ""


# ──────────────────────────────────────────────
# 통합 Analyst Agent
# ──────────────────────────────────────────────

class AnalystAgent:
    """
    애널리스트 에이전트

    DART/뉴스 투자 근거와 뉴스/포럼 심리를 모아 헤게모니 점수를 산출합니다.
    """

    def __init__(self):
        self._instruct_llm = None   # 필요 시에만 로드 (Lazy)
        self._thinking_llm = None   # 필요 시에만 로드 (Lazy)

        # Source-aware RAG tools (canonical retriever 기반)
        self.rag_tool = RAGSearchTool(top_k=5)
        self.rag_tool_evidence = RAGSearchTool(
            top_k=5, source_types=["dart", "news"], intent="investment"
        )
        self.rag_tool_news = RAGSearchTool(
            top_k=5, source_types=["news", "forum"], intent="sentiment"
        )

        # 내부 추적용
        self._last_evidence_source = "none"
        self._last_news_source = "none"

    # ── LLM Lazy Loading ──

    @property
    def instruct_llm(self):
        if self._instruct_llm is None:
            self._instruct_llm = get_summary_llm()
        return self._instruct_llm

    @property
    def thinking_llm(self):
        if self._thinking_llm is None:
            self._thinking_llm = get_analyst_llm()
        return self._thinking_llm

    # ── 공개 API ──

    def full_analysis(self, stock_name: str, stock_code: str) -> AnalystScore:
        """
        전체 분석 수행 (데이터 수집 → Thinking 분석)

        Args:
            stock_name: 종목명
            stock_code: 종목코드

        Returns:
            AnalystScore 데이터클래스
        """
        # Phase 1: 데이터 수집 (도구 호출 중심, LLM 최소화)
        print(f"🔍 [Analyst] {stock_name} 데이터 수집 중...")
        research_result = self._collect_research(stock_name, stock_code)

        # Phase 2: 통합 분석 (Thinking LLM 1회 호출)
        print(f"🧠 [Analyst] {stock_name} 헤게모니 통합 분석 중...")
        score = self._analyze_hegemony(research_result)

        return score

    def analyze_stock(self, stock_name: str, stock_code: str) -> str:
        """종목 분석 수행 (보고서 형식 반환)"""
        score = self.full_analysis(stock_name, stock_code)
        return self.generate_report(score, stock_name)

    # ──────────────────────────────────────────────
    # Phase 1: 데이터 수집
    # ──────────────────────────────────────────────

    def _collect_research(self, stock_name: str, stock_code: str) -> ResearchResult:
        """
        종목에 대한 종합 리서치 수행

        도구 호출 위주로 데이터를 수집하고, 필요한 경우에만
        Instruct LLM을 호출하여 요약합니다.
        """
        result = ResearchResult(stock_name=stock_name, stock_code=stock_code)

        # 1. DART/뉴스 투자 근거 검색
        print(f"📄 {stock_name} DART/뉴스 투자 근거 검색 중...")
        result.evidence_summary, result.evidence_sources = self._search_evidence(stock_name)

        # 2. 뉴스/포럼 검색
        print(f"📰 {stock_name} 뉴스/포럼 검색 중...")
        result.news_summary = self._search_news(stock_name)

        # 3. 품질 평가
        result.data_sources = self._collect_data_sources()
        result.evaluate_quality()

        quality_icon = "✅" if result.quality_score >= 60 else "⚠️"
        print(f"\n{quality_icon} 정보 품질: {result.quality_grade} ({result.quality_score}/100)")
        if result.quality_warnings:
            for w in result.quality_warnings:
                print(f"   ⚠️ {w}")

        return result

    # ──────────────────────────────────────────────
    # Phase 2: 헤게모니 분석
    # ──────────────────────────────────────────────

    def _analyze_hegemony(self, research_result: ResearchResult) -> AnalystScore:
        """
        헤게모니 분석 수행 (Thinking LLM 1회 호출)
        """
        stock_name = research_result.stock_name
        research_summary = research_result.to_analysis_prompt()
        print(f"🧠 {stock_name} 헤게모니 분석 중 (Thinking 모델)...")

        analysis_prompt = load_prompt(
            "analyst",
            "analysis",
            stock_name=stock_name,
            stock_code=research_result.stock_code,
            research_summary=research_summary,
            quality_grade=research_result.quality_grade,
            quality_score=research_result.quality_score,
            quality_warnings="\n".join(f"- {w}" for w in research_result.quality_warnings) or "- 없음",
        )

        try:
            response = self.thinking_llm.invoke(analysis_prompt)
            response_text = response.content.strip()

            # JSON 파싱
            json_match = re.search(r'\{[\s\S]*\}', response_text)
            if json_match:
                result = json.loads(json_match.group())
            else:
                raise ValueError("JSON 형식 응답 없음")

            moat_score = min(50, max(0, int(result.get("moat_score", 25))))
            growth_score = min(50, max(0, int(result.get("growth_score", 25))))

            return AnalystScore(
                moat_score=moat_score,
                growth_score=growth_score,
                total_score=moat_score + growth_score,
                moat_reason=result.get("moat_reason", ""),
                growth_reason=result.get("growth_reason", ""),
                evidence_summary=research_result.evidence_summary[:500],
                final_opinion=result.get("final_opinion", ""),
                competitive_advantage=result.get("competitive_advantage", ""),
                risk_factors=result.get("risk_factors", ""),
                hegemony_grade=result.get("hegemony_grade", "C"),
                detailed_reasoning=result.get("detailed_reasoning", ""),
            )

        except Exception as e:
            logger.exception(f"헤게모니 분석 오류: {e}")
            print(f"❌ 분석 오류: {e}")
            return AnalystScore(
                moat_score=25,
                growth_score=25,
                total_score=50,
                moat_reason="분석 오류로 기본값 적용",
                growth_reason="분석 오류로 기본값 적용",
                evidence_summary=research_result.evidence_summary[:500],
                final_opinion="데이터 부족으로 중립 의견",
                competitive_advantage="판단 불가",
                risk_factors="분석 오류",
                hegemony_grade="C",
                detailed_reasoning=f"오류 발생: {str(e)}",
            )

    def generate_report(self, score: AnalystScore, stock_name: str) -> str:
        """분석 결과를 마크다운 보고서 형식으로 출력"""
        return f"""
# {stock_name} 헤게모니 분석 보고서

## 📊 점수 요약
| 항목 | 점수 | 비중 |
|------|------|------|
| 독점력 (Moat) | **{score.moat_score}** / 50 | 50% |
| 성장성 (Growth) | **{score.growth_score}** / 50 | 50% |
| **총점** | **{score.total_score}** / 100 | 100% |

## 🏆 헤게모니 등급: {score.hegemony_grade}

## 💡 핵심 판단
> {score.final_opinion}

---

## 1. 독점력/경제적 해자 분석
{score.moat_reason}

**경쟁 우위:** {score.competitive_advantage}

## 2. 성장성 분석
{score.growth_reason}

## 3. 리스크 요인
{score.risk_factors}

---

## 📝 상세 추론 과정
{score.detailed_reasoning}
"""

    # ──────────────────────────────────────────────
    # 도구 기반 데이터 수집 헬퍼 (LLM 호출 최소화)
    # ──────────────────────────────────────────────

    def _is_empty_result(self, text: str) -> bool:
        """검색 결과가 비어있거나 유효하지 않은지 판단"""
        if not text or not text.strip():
            return True
        empty_markers = [
            "찾을 수 없습니다", "관련 문서를 찾을 수 없습니다",
            "검색 결과가 없습니다", "관련 뉴스 없음",
            "retrieval 인덱스가 없습니다", "데이터 디렉터리가 없습니다",
            "오류", "실패",
        ]
        return any(marker in text for marker in empty_markers)

    def _collect_data_sources(self) -> Dict:
        """각 카테고리별 데이터 소스 수집"""
        return {
            "evidence": self._last_evidence_source,
            "news": self._last_news_source,
        }

    def _search_evidence(self, stock_name: str) -> Tuple[str, List[str]]:
        """
        DART/뉴스 기반 투자 근거 검색 및 요약

        [최적화] RAG 검색 결과를 LLM 없이 직접 반환.
        요약은 Thinking 모델이 통합 프롬프트에서 수행.
        단, 결과가 3000자 초과 시에만 Instruct LLM으로 1회 요약.
        """
        self._last_evidence_source = "none"

        # RAG 검색 (source: dart, news)
        try:
            query = f"{stock_name} 실적 전망 목표주가 투자의견"
            context = self.rag_tool_evidence._run(query)

            if not self._is_empty_result(context):
                self._last_evidence_source = "rag"
                sources = self._extract_sources(context)
                self._log_retrieval_debug(
                    "evidence",
                    query,
                    self._extract_retrieval_hits(context),
                )
                # 3000자 이하면 원시 텍스트 그대로 전달 (LLM 호출 절약)
                if len(context) <= 3000:
                    return context, sources
                # 너무 길면 핵심만 Instruct LLM으로 압축 (1회)
                summary = self._summarize_evidence(stock_name, context)
                return summary, sources
        except Exception as e:
            print(f"   ⚠️ DART/뉴스 투자 근거 RAG 오류: {e}")

        return "DART/뉴스 투자 근거를 확보하지 못했습니다. (RAG 결과 없음)", []

    def _extract_sources(self, context: str) -> List[str]:
        """컨텍스트에서 출처 정보 추출 (canonical + legacy 포맷 모두 지원)"""
        import re
        sources = []
        for line in context.split("\n"):
            # Canonical format: source=dart, source=news, source=forum, ...
            m = re.search(r'source=([a-z_]+)', line)
            if m:
                src = m.group(1).strip()
                if src and src not in sources:
                    sources.append(src)
                continue
            # Legacy format: (출처: xxx, ...)
            if "출처:" in line:
                try:
                    src = line.split("출처:")[1].split(",")[0].strip().rstrip(")")
                    if src and src not in sources:
                        sources.append(src)
                except Exception:
                    pass
        return sources

    def _extract_retrieval_hits(self, context: str) -> List[Dict[str, str]]:
        hits: List[Dict[str, str]] = []
        pattern = re.compile(
            r"source=(?P<source>[a-z_]+).*?title=(?P<title>[^,\n]*)",
            re.IGNORECASE,
        )
        for line in context.splitlines():
            if "source=" not in line:
                continue
            match = pattern.search(line)
            if not match:
                continue
            hits.append(
                {
                    "source": match.group("source").strip(),
                    "title": match.group("title").strip(),
                }
            )
        return hits

    def _extract_llm_text(self, response) -> str:
        content = getattr(response, "content", "")
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content") or ""
                    if text:
                        parts.append(str(text))
            return "\n".join(part.strip() for part in parts if str(part).strip()).strip()

        if content is None:
            return ""

        return str(content).strip()

    def _log_retrieval_debug(self, label: str, query: str, hits: List[Dict[str, str]]) -> None:
        preview = ", ".join(
            f"{row['source']}:{row['title'][:30] or '(untitled)'}"
            for row in hits[:3]
        )
        logger.info(
            "[Retrieval:%s] query=%s hits=%s preview=%s",
            label,
            query,
            len(hits),
            preview or "-",
        )

    def _summarize_evidence(self, stock_name: str, context: str) -> str:
        """DART/뉴스 투자 근거를 LLM으로 요약 (3000자 초과 시에만 호출)"""
        summary_prompt = f"""
다음은 '{stock_name}'에 대한 DART 공시와 뉴스 기반 투자 근거입니다.
핵심 내용을 5줄 이내로 요약해주세요.

[DART/뉴스 내용]
{context[:3000]}

[요약 포인트]
- 실적 또는 사업 변화
- 공시상 주요 이벤트
- 핵심 실적 전망
- 주요 리스크
"""
        response = self.instruct_llm.invoke(summary_prompt)
        return response.content

    def _search_news(self, stock_name: str) -> str:
        """
        최신 뉴스/포럼 검색

        현재 파이프라인 산출물인 news/forum canonical RAG만 사용합니다.
        """
        self._last_news_source = "none"

        try:
            rag_query = f"{stock_name} 뉴스 시장 이슈 최근 동향"
            context = self.rag_tool_news._run(rag_query)

            if not self._is_empty_result(context):
                self._last_news_source = "rag"
                self._log_retrieval_debug(
                    "news",
                    rag_query,
                    self._extract_retrieval_hits(context),
                )
                return context[:500] + "\n\n[데이터 출처: RAG 저장 문서 — 실시간 뉴스 아님]"
        except Exception as e:
            print(f"   ⚠️ 뉴스/포럼 RAG 검색 실패: {e}")

        return "뉴스/포럼 정보를 확보하지 못했습니다. (RAG 결과 없음)"

# 사용 예시
if __name__ == "__main__":
    agent = AnalystAgent()

    print("=" * 60)
    print("삼성전자 헤게모니 분석 (통합 Analyst Agent)")
    print("=" * 60)

    score = agent.full_analysis("삼성전자", "005930")

    print(f"\n📊 분석 결과:")
    print(f"   헤게모니 등급: {score.hegemony_grade}")
    print(f"   독점력: {score.moat_score}/50점")
    print(f"   성장성: {score.growth_score}/50점")
    print(f"   총점: {score.total_score}/100점")
    print(f"\n💡 총평: {score.final_opinion}")
    print(f"\n🛡️ 경쟁 우위: {score.competitive_advantage}")
    print(f"⚠️ 리스크: {score.risk_factors}")
