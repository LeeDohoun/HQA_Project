# 파일: src/agents/analyst.py
"""
Analyst Agent (애널리스트 에이전트) — 통합 버전

기존 Researcher + Strategist를 단일 에이전트로 통합하여
LLM 호출 비용을 절감합니다.

LLM 호출 흐름 (구버전 → 신버전):
  [구] Researcher(Instruct) × 5회 + Strategist(Thinking) × 1회 = 최대 6회
  [신] 도구 기반 데이터 수집 (LLM 0~2회) + 통합 분석(Thinking) × 1회 = 최대 3회

역할:
- 증권사 리포트 RAG 검색 (도구 호출, LLM 불필요)
- 웹 검색 폴백 (도구 호출, LLM 불필요)
- Vision 차트/그래프 분석 (선택적 LLM)
- 수집된 원시 데이터를 하나의 Thinking 프롬프트에 주입
- 독점력(Moat) + 성장성(Growth) + 최종 헤게모니 등급 도출
"""

import json
import logging
import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

from src.agents.llm_config import get_instruct_llm, get_thinking_llm, VisionAnalyzer
from src.utils.prompt_loader import load_prompt_optional
from src.agents.context import AgentContextPacket, EvidenceItem

# RAG 검색 도구 (Canonical Retriever 통합)
from src.tools.rag_tool import RAGSearchTool, get_canonical_retriever

# 웹 검색 도구 (선택적)
try:
    from src.tools.web_search_tool import WebSearchTool, NewsSearchTool
    WEB_SEARCH_AVAILABLE = True
except ImportError:
    WEB_SEARCH_AVAILABLE = False

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 데이터 클래스
# ──────────────────────────────────────────────

@dataclass
class ResearchResult:
    """리서치 결과 데이터 클래스 (하위 호환성 유지)"""
    stock_name: str
    stock_code: str

    # 리포트 분석
    report_summary: str = ""
    report_sources: List[str] = field(default_factory=list)

    # 차트/이미지 분석
    chart_analysis: str = ""
    chart_count: int = 0

    # 뉴스/정책 정보
    news_summary: str = ""
    policy_summary: str = ""

    # 산업 동향
    industry_summary: str = ""

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

        # 리포트 (40점) — 가장 중요
        if _has_content(self.report_summary):
            score += 40
        else:
            self.quality_warnings.append("증권사 리포트 부재 — 정성적 분석의 신뢰도가 낮을 수 있음")

        # 뉴스 (25점)
        if _has_content(self.news_summary):
            score += 25
        else:
            self.quality_warnings.append("최신 뉴스 부재 — 시장 센티먼트 파악 제한적")

        # 산업동향 (20점)
        if _has_content(self.industry_summary):
            score += 20
        else:
            self.quality_warnings.append("산업 동향 부재 — 섹터 분석 제한적")

        # 정책 (15점)
        if _has_content(self.policy_summary):
            score += 15
        else:
            self.quality_warnings.append("정책/규제 정보 부재")

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

## 1. 증권사 리포트 요약
{self.report_summary or "리포트 정보 없음"}

## 2. 차트/그래프 분석
{self.chart_analysis or "차트 데이터 없음"}
- 분석된 차트 수: {self.chart_count}개

## 3. 최신 뉴스
{self.news_summary or "뉴스 정보 없음"}

## 4. 정책/규제 동향
{self.policy_summary or "정책 정보 없음"}

## 5. 산업 동향
{self.industry_summary or "산업 정보 없음"}

---
리서치 시점: {self.timestamp}
"""

    # 하위 호환: 구버전 Strategist 코드가 호출하는 메서드 유지
    to_strategist_prompt = to_analysis_prompt


@dataclass
class HegemonyScore:
    """헤게모니 분석 점수 (구 strategist.py 호환)"""
    moat_score: int
    growth_score: int
    total_score: int
    moat_analysis: str
    growth_analysis: str
    competitive_advantage: str
    risk_factors: str
    policy_impact: str
    hegemony_grade: str
    final_opinion: str
    detailed_reasoning: str


@dataclass
class AnalystScore:
    """애널리스트 분석 점수"""
    moat_score: int        # 독점력 (0-40점)
    growth_score: int      # 성장성 (0-30점)
    total_score: int       # 총점 (0-70점)
    moat_reason: str
    growth_reason: str
    report_summary: str
    image_analysis: str    # Vision 분석 결과
    final_opinion: str

    # 추가 필드
    hegemony_grade: str = "C"
    competitive_advantage: str = ""
    risk_factors: str = ""
    policy_impact: str = ""
    detailed_reasoning: str = ""
    analysis_packet: Dict = field(default_factory=dict)


# ──────────────────────────────────────────────
# 통합 Analyst Agent
# ──────────────────────────────────────────────

class AnalystAgent:
    """
    통합 애널리스트 에이전트

    구버전에서 Researcher(Instruct) + Strategist(Thinking) 두 개의 에이전트를
    각각 인스턴스화하여 LLM을 2종 로드 + 6회 호출하던 구조를:

    1) 데이터 수집: 도구(RAG/웹) 호출만으로 원시 데이터 확보 (LLM 호출 0~1회)
    2) 통합 분석:  Thinking LLM 1회 호출로 요약 + 헤게모니 판단을 한 방에 수행

    으로 줄여 에이전트 호출 비용을 대폭 절감합니다.
    """

    def __init__(self):
        self._instruct_llm = None   # 필요 시에만 로드 (Lazy)
        self._thinking_llm = None   # 필요 시에만 로드 (Lazy)
        self.vision_analyzer = VisionAnalyzer()

        # Source-aware RAG tools (canonical retriever 기반)
        self.rag_tool = RAGSearchTool(top_k=5)
        self.rag_tool_reports = RAGSearchTool(
            top_k=5, source_types=["report", "dart", "news"], intent="investment"
        )
        self.rag_tool_news = RAGSearchTool(
            top_k=5, source_types=["news", "general_news", "forum"], intent="sentiment"
        )
        self.rag_tool_policy = RAGSearchTool(
            top_k=5, source_types=["dart", "news", "report"], intent="policy"
        )
        self.rag_tool_industry = RAGSearchTool(
            top_k=5, source_types=["report", "news"], intent="industry"
        )

        # 내부 추적용
        self._last_report_source = "none"
        self._last_news_source = "none"
        self._last_policy_source = "none"
        self._last_industry_source = "none"

    # ── LLM Lazy Loading ──

    @property
    def instruct_llm(self):
        if self._instruct_llm is None:
            self._instruct_llm = get_instruct_llm()
        return self._instruct_llm

    @property
    def thinking_llm(self):
        if self._thinking_llm is None:
            self._thinking_llm = get_thinking_llm()
        return self._thinking_llm

    @property
    def llm(self):
        """하위 호환: supervisor.py에서 strategist.llm.invoke() 접근 지원"""
        return self.thinking_llm

    # ── 하위 호환: 구버전 코드에서 .researcher / .strategist 접근 시 동작 ──

    @property
    def researcher(self):
        """하위 호환: graph.py 등에서 agent.researcher.research() 호출 지원"""
        return self

    @property
    def strategist(self):
        """하위 호환: graph.py 등에서 agent.strategist.analyze_hegemony() 호출 지원"""
        return self

    # ── 공개 API ──

    def full_analysis(self, stock_name: str, stock_code: str) -> AnalystScore:
        """
        전체 분석 수행 (데이터 수집 → 통합 Thinking 분석)

        기존 대비 LLM 호출 횟수: 6회 → 최대 3회

        Args:
            stock_name: 종목명
            stock_code: 종목코드

        Returns:
            AnalystScore 데이터클래스
        """
        # Phase 1: 데이터 수집 (도구 호출 중심, LLM 최소화)
        print(f"🔍 [Analyst] {stock_name} 데이터 수집 중...")
        research_result = self.research(stock_name, stock_code)

        # Phase 2: 통합 분석 (Thinking LLM 1회 호출)
        print(f"🧠 [Analyst] {stock_name} 헤게모니 통합 분석 중...")
        hegemony = self.analyze_hegemony(research_result)

        packet = self._build_context_packet(stock_name, stock_code, research_result, hegemony)

        return AnalystScore(
            moat_score=hegemony.moat_score,
            growth_score=hegemony.growth_score,
            total_score=hegemony.total_score,
            moat_reason=hegemony.moat_analysis,
            growth_reason=hegemony.growth_analysis,
            report_summary=research_result.report_summary[:500],
            image_analysis=research_result.chart_analysis[:500],
            final_opinion=hegemony.final_opinion,
            hegemony_grade=hegemony.hegemony_grade,
            competitive_advantage=hegemony.competitive_advantage,
            risk_factors=hegemony.risk_factors,
            policy_impact=hegemony.policy_impact,
            detailed_reasoning=hegemony.detailed_reasoning,
            analysis_packet=packet.to_dict(),
        )

    def analyze_stock(self, stock_name: str, stock_code: str) -> str:
        """종목 분석 수행 (보고서 형식 반환)"""
        score = self.full_analysis(stock_name, stock_code)
        return self.generate_report(
            HegemonyScore(
                moat_score=score.moat_score,
                growth_score=score.growth_score,
                total_score=score.total_score,
                moat_analysis=score.moat_reason,
                growth_analysis=score.growth_reason,
                competitive_advantage=score.competitive_advantage,
                risk_factors=score.risk_factors,
                policy_impact=score.policy_impact,
                hegemony_grade=score.hegemony_grade,
                final_opinion=score.final_opinion,
                detailed_reasoning=score.detailed_reasoning,
            ),
            stock_name,
        )

    # ──────────────────────────────────────────────
    # Phase 1: 데이터 수집 (구 Researcher 로직 통합)
    # ──────────────────────────────────────────────

    def research(self, stock_name: str, stock_code: str) -> ResearchResult:
        """
        종목에 대한 종합 리서치 수행

        도구 호출 위주로 데이터를 수집하고, 필요한 경우에만
        Instruct LLM을 호출하여 요약합니다.
        """
        result = ResearchResult(stock_name=stock_name, stock_code=stock_code)

        # 1. 증권사 리포트 검색 (RAG → 웹 폴백)
        print(f"📄 {stock_name} 리포트 검색 중...")
        result.report_summary, result.report_sources = self._search_reports(stock_name)

        # 2. 차트/그래프 분석
        print(f"📊 {stock_name} 차트 분석 중...")
        result.chart_analysis, result.chart_count = self._analyze_charts(stock_name)

        # 3. 뉴스 검색 (웹 → RAG 폴백) — LLM 요약 제거, 원시 텍스트 전달
        print(f"📰 {stock_name} 뉴스 검색 중...")
        result.news_summary = self._search_news(stock_name)

        # 4. 정책/규제 검색 (웹 → RAG 폴백)
        print(f"📋 {stock_name} 관련 정책 검색 중...")
        result.policy_summary = self._search_policy(stock_name)

        # 5. 산업 동향 검색 (RAG → 웹 폴백)
        print(f"🏭 {stock_name} 산업 동향 검색 중...")
        result.industry_summary = self._search_industry(stock_name)

        # 6. 품질 평가
        result.data_sources = self._collect_data_sources()
        result.evaluate_quality()

        quality_icon = "✅" if result.quality_score >= 60 else "⚠️"
        print(f"\n{quality_icon} 정보 품질: {result.quality_grade} ({result.quality_score}/100)")
        if result.quality_warnings:
            for w in result.quality_warnings:
                print(f"   ⚠️ {w}")

        return result

    def quick_research(self, stock_name: str, stock_code: str) -> ResearchResult:
        """빠른 리서치 (정보 수집만, 판단 없음)"""
        return self.research(stock_name, stock_code)

    def quick_search(self, query: str) -> Dict:
        """빠른 검색 (특정 쿼리로)"""
        # Plan A: RAG
        context = self.rag_tool._run(query)

        if not self._is_empty_result(context):
            hits = self._extract_retrieval_hits(context)
            self._log_retrieval_debug("quick_search", query, hits)
            return {
                "query": query,
                "context": context[:1000],
                "has_results": True,
                "source": "rag",
                "hits": hits,
            }

        # Plan B: 웹 검색 폴백
        if WEB_SEARCH_AVAILABLE:
            try:
                from src.tools.web_search_tool import search_web
                results = search_web(query, max_results=3)
                if results:
                    web_context = "\n".join([
                        f"- {r.get('title', '')}: {r.get('snippet', '')}"
                        for r in results[:3]
                    ])
                    return {
                        "query": query,
                        "context": web_context[:1000],
                        "has_results": True,
                        "source": "web",
                        "hits": [],
                    }
            except Exception:
                pass

        return {
            "query": query,
            "context": "",
            "has_results": False,
            "source": "none",
            "hits": [],
        }

    def answer_question(self, question: str, max_context_chars: int = 2500) -> Dict:
        """검색 결과를 근거로 단문 QA를 수행합니다."""
        search = self.quick_search(question)
        if not search["has_results"]:
            raise ValueError(
                "retrieval 결과가 없어 답변을 생성할 수 없습니다. "
                "데이터 연결 상태를 먼저 점검하세요."
            )

        if search["source"] != "rag":
            raise ValueError(
                "검색 결과가 웹 폴백에만 의존하고 있습니다. "
                "이 데모는 저장된 RAG 데이터 1건 이상이 필요합니다."
            )

        prompt = f"""
당신은 한국 주식 분석 어시스턴트입니다.
아래 검색 컨텍스트만 근거로 질문에 답하세요.
근거가 부족하면 부족하다고 분명히 말하세요.

[질문]
{question}

[검색 컨텍스트]
{search['context'][:max_context_chars]}

[답변 형식]
1. 핵심 답변 2~4문장
2. 근거 출처를 source 기준으로 한 줄 요약
"""
        response = self.instruct_llm.invoke(prompt)
        answer = self._extract_llm_text(response)

        if self._is_empty_result(answer):
            logger.warning(
                "빈 LLM 응답 감지. 짧은 컨텍스트로 재시도합니다. query=%s",
                question,
            )
            retry_prompt = f"""
당신은 한국 주식 분석 어시스턴트입니다.
반드시 최종 답변만 출력하고, 생각 과정은 출력하지 마세요.
아래 검색 문서 2건만 근거로 3문장 이내로 답하세요.

[질문]
{question}

[검색 문서]
{search['context'][:1200]}

[출력 규칙]
- 핵심 답변 2~3문장
- 마지막 줄에 '근거:'로 시작하는 출처 요약 1줄
"""
            response = self.instruct_llm.invoke(retry_prompt)
            answer = self._extract_llm_text(response)

        if self._is_empty_result(answer):
            logger.warning(
                "LLM 응답이 계속 비어 있어 RAG 폴백 답변을 사용합니다. query=%s",
                question,
            )
            answer = self._build_rag_fallback_answer(search)

        return {
            "question": question,
            "answer": answer,
            "search_source": search["source"],
            "retrieved_hits": search["hits"],
            "context_excerpt": search["context"],
        }

    # ──────────────────────────────────────────────
    # Phase 2: 통합 분석 (구 Strategist 로직 통합)
    # ──────────────────────────────────────────────

    def analyze_hegemony(self, research_result: ResearchResult) -> HegemonyScore:
        """
        헤게모니 분석 수행 (Thinking LLM 1회 호출)

        구버전에서 Researcher → to_strategist_prompt() → Strategist 로
        데이터를 넘기며 2회 호출하던 것을,
        원시 데이터를 Thinking 프롬프트에 직접 넣어 1회로 통합합니다.
        """
        stock_name = research_result.stock_name
        research_summary = research_result.to_analysis_prompt()
        print(f"🧠 {stock_name} 헤게모니 분석 중 (Thinking 모델)...")

        analysis_prompt = load_prompt_optional(
            "analyst",
            "analysis",
            fallback=self._analysis_fallback_prompt(),
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

            moat_score = min(40, max(0, int(result.get("moat_score", 20))))
            growth_score = min(30, max(0, int(result.get("growth_score", 15))))

            return HegemonyScore(
                moat_score=moat_score,
                growth_score=growth_score,
                total_score=moat_score + growth_score,
                moat_analysis=result.get("moat_analysis", ""),
                growth_analysis=result.get("growth_analysis", ""),
                competitive_advantage=result.get("competitive_advantage", ""),
                risk_factors=result.get("risk_factors", ""),
                policy_impact=result.get("policy_impact", ""),
                hegemony_grade=result.get("hegemony_grade", "C"),
                final_opinion=result.get("final_opinion", ""),
                detailed_reasoning=result.get("detailed_reasoning", ""),
            )

        except Exception as e:
            logger.exception(f"헤게모니 분석 오류: {e}")
            print(f"❌ 분석 오류: {e}")
            return HegemonyScore(
                moat_score=20,
                growth_score=15,
                total_score=35,
                moat_analysis="분석 오류로 기본값 적용",
                growth_analysis="분석 오류로 기본값 적용",
                competitive_advantage="판단 불가",
                risk_factors="분석 오류",
                policy_impact="판단 불가",
                hegemony_grade="C",
                final_opinion="데이터 부족으로 중립 의견",
                detailed_reasoning=f"오류 발생: {str(e)}",
            )

    def _analysis_fallback_prompt(self) -> str:
        """분석용 기본 프롬프트"""
        return """
당신은 20년 경력의 베테랑 투자 전략가입니다.
다음 리서치 자료를 바탕으로 '{stock_name}'({stock_code})의 헤게모니(경제적 해자)를 분석하세요.

{research_summary}

---

다음 관점에서 깊이 있게 분석하세요:

## 1. 독점력/경제적 해자 분석 (0-40점)
- 시장 점유율과 지배력
- 진입 장벽 (기술, 자본, 규모)
- 가격 결정력 (Pricing Power)
- 브랜드/네트워크 효과
- 전환 비용 (Switching Cost)

## 2. 성장성 분석 (0-30점)
- 미래 산업 연관성 (AI, 로봇, 친환경 등)
- 매출 성장 구조 (일회성 vs 구조적)
- TAM(Total Addressable Market) 확장 가능성
- R&D 투자와 기술 리더십

## 3. 정책/규제 영향
- 정부 지원 정책의 수혜 여부
- 규제 리스크
- 글로벌 통상 환경 영향

## 4. 경쟁 구도 분석
- 주요 경쟁사 대비 포지션
- 경쟁 심화/완화 전망
- 대체재 위협

## 5. 리스크 요인
- 산업 사이클 리스크
- 기술 변화 리스크
- 지정학적 리스크

---

[⚠️ 중요: 데이터 신뢰도에 따른 행동 강령]
품질 등급이 낮을수록 더 보수적으로 판단하세요.

- A: 확보된 데이터를 근거로 확신에 찬 어조
- B: 정상 분석
- C: 불확실성 명시
- D: 신뢰도 낮음, 두 단계 보수화

분석 결과를 다음 JSON 형식으로 출력하세요:
{
    "moat_score": <0-40 정수>,
    "moat_analysis": "<독점력 분석 3-5문장>",
    "growth_score": <0-30 정수>,
    "growth_analysis": "<성장성 분석 3-5문장>",
    "competitive_advantage": "<경쟁 우위 핵심 1-2문장>",
    "risk_factors": "<주요 리스크 2-3가지>",
    "policy_impact": "<정책 영향 1-2문장>",
    "hegemony_grade": "<A/B/C/D/F 중 하나>",
    "final_opinion": "<한 줄 총평>",
    "detailed_reasoning": "<상세 추론 과정 5-10문장>"
}

JSON만 출력하세요.
"""

    def _build_context_packet(
        self,
        stock_name: str,
        stock_code: str,
        research_result: ResearchResult,
        hegemony: HegemonyScore,
    ) -> AgentContextPacket:
        """Risk Manager로 넘길 구조화 컨텍스트 생성"""
        key_points = [point for point in [
            hegemony.moat_analysis.split("。")[0] if hegemony.moat_analysis else "",
            hegemony.growth_analysis.split("。")[0] if hegemony.growth_analysis else "",
            research_result.report_sources[0] if research_result.report_sources else "",
        ] if point]

        risks = [item.strip(" -•") for item in re.split(r"[,\n]", hegemony.risk_factors) if item.strip()]
        evidence = [
            EvidenceItem(
                source="rag",
                title="증권사 리포트",
                snippet=research_result.report_summary[:240],
            ),
            EvidenceItem(
                source="web",
                title="뉴스/정책",
                snippet=(research_result.news_summary or research_result.policy_summary)[:240],
            ),
        ]

        return AgentContextPacket(
            agent_name="analyst",
            stock_name=stock_name,
            stock_code=stock_code,
            summary=hegemony.final_opinion,
            key_points=key_points[:5],
            risks=risks[:5],
            catalysts=[hegemony.policy_impact] if hegemony.policy_impact else [],
            contrarian_view=hegemony.risk_factors,
            evidence=evidence,
            score=hegemony.total_score,
            confidence=research_result.quality_score,
            grade=hegemony.hegemony_grade,
            next_action="risk_manager_review",
            source_tags=[
                "rag",
                "web_search",
                "vision" if research_result.chart_analysis else "text",
            ],
        )

    def generate_report(self, score: HegemonyScore, stock_name: str) -> str:
        """분석 결과를 마크다운 보고서 형식으로 출력"""
        return f"""
# {stock_name} 헤게모니 분석 보고서

## 📊 점수 요약
| 항목 | 점수 | 비중 |
|------|------|------|
| 독점력 (Moat) | **{score.moat_score}** / 40 | 57% |
| 성장성 (Growth) | **{score.growth_score}** / 30 | 43% |
| **총점** | **{score.total_score}** / 70 | 100% |

## 🏆 헤게모니 등급: {score.hegemony_grade}

## 💡 핵심 판단
> {score.final_opinion}

---

## 1. 독점력/경제적 해자 분석
{score.moat_analysis}

**경쟁 우위:** {score.competitive_advantage}

## 2. 성장성 분석
{score.growth_analysis}

## 3. 정책 영향
{score.policy_impact}

## 4. 리스크 요인
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
            "관련 정책 정보 없음", "관련 리포트를 찾을 수 없습니다",
            "retrieval 인덱스가 없습니다", "데이터 디렉터리가 없습니다",
            "오류", "실패",
        ]
        return any(marker in text for marker in empty_markers)

    def _collect_data_sources(self) -> Dict:
        """각 카테고리별 데이터 소스 수집"""
        return {
            "reports": self._last_report_source,
            "news": self._last_news_source,
            "policy": self._last_policy_source,
            "industry": self._last_industry_source,
        }

    def _search_reports(self, stock_name: str) -> Tuple[str, List[str]]:
        """
        증권사 리포트 검색 및 요약
        Plan A: RAG → Plan B: 웹 검색 폴백

        [최적화] RAG 검색 결과를 LLM 없이 직접 반환.
        요약은 Thinking 모델이 통합 프롬프트에서 수행.
        단, 결과가 3000자 초과 시에만 Instruct LLM으로 1회 요약.
        """
        self._last_report_source = "none"

        # Plan A: RAG 검색 (source: report, dart, news)
        try:
            query = f"{stock_name} 실적 전망 목표주가 투자의견"
            context = self.rag_tool_reports._run(query)

            if not self._is_empty_result(context):
                self._last_report_source = "rag"
                sources = self._extract_sources(context)
                self._log_retrieval_debug(
                    "reports",
                    query,
                    self._extract_retrieval_hits(context),
                )
                # 3000자 이하면 원시 텍스트 그대로 전달 (LLM 호출 절약)
                if len(context) <= 3000:
                    return context, sources
                # 너무 길면 핵심만 Instruct LLM으로 압축 (1회)
                summary = self._summarize_report(stock_name, context)
                return summary, sources
            else:
                print(f"   ℹ️ RAG 리포트 결과 없음 → 웹 검색 폴백")
        except Exception as e:
            print(f"   ⚠️ RAG 리포트 오류: {e} → 웹 검색 폴백")

        # Plan B: 웹 검색 폴백
        if WEB_SEARCH_AVAILABLE:
            try:
                from src.tools.web_search_tool import search_web
                web_query = f"{stock_name} 증권사 리포트 목표주가 투자의견"
                results = search_web(web_query, max_results=5)

                if results:
                    self._last_report_source = "web"
                    web_context = "\n".join([
                        f"- [{r.get('title', '')}] {r.get('snippet', '')}"
                        for r in results[:5]
                    ])
                    # 웹 결과는 이미 짧으므로 LLM 호출 없이 원시 텍스트 전달
                    web_context += "\n\n[데이터 출처: 웹 검색 자료 — 증권사 원문 리포트 대비 정확도 제한적]"
                    web_sources = [r.get("url", "") for r in results if r.get("url")]
                    return web_context, web_sources
            except Exception as e:
                print(f"   ⚠️ 웹 검색 폴백도 실패: {e}")

        return "증권사 리포트를 확보하지 못했습니다. (RAG/웹 모두 실패)", []

    def _extract_sources(self, context: str) -> List[str]:
        """컨텍스트에서 출처 정보 추출 (canonical + legacy 포맷 모두 지원)"""
        import re
        sources = []
        for line in context.split("\n"):
            # Canonical format: source=report, source=dart, ...
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

    def _extract_context_snippet(self, context: str, max_chars: int = 360) -> str:
        lines: List[str] = []
        for raw_line in context.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("===") or line.startswith("[문서 "):
                continue
            if "source=" in line and "title=" in line:
                continue
            lines.append(line)
            if sum(len(row) for row in lines) >= max_chars:
                break

        snippet = re.sub(r"\s+", " ", " ".join(lines)).strip()
        return snippet[:max_chars].rstrip(" ,.;")

    def _build_rag_fallback_answer(self, search: Dict) -> str:
        hits = search.get("hits", [])[:3]
        snippet = self._extract_context_snippet(search.get("context", ""))
        source_summary = "; ".join(
            f"{hit.get('source', 'unknown')} - {hit.get('title', '(untitled)')}"
            for hit in hits
        )

        if snippet:
            return (
                f"저장된 RAG 문서를 기준으로 보면 {snippet}. "
                f"추가 정밀 분석이 필요하면 더 많은 리포트 데이터가 필요합니다.\n\n"
                f"근거: {source_summary or '검색된 RAG 문서'}"
            )

        return (
            f"저장된 RAG 문서 {len(hits)}건을 검색했지만 문장형 응답 생성이 불안정했습니다. "
            f"검색 근거는 다음과 같습니다: {source_summary or '검색된 RAG 문서'}"
        )

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

    def _summarize_report(self, stock_name: str, context: str) -> str:
        """리포트 내용을 LLM으로 요약 (3000자 초과 시에만 호출)"""
        summary_prompt = f"""
다음은 '{stock_name}'에 대한 증권사 리포트 내용입니다.
핵심 내용을 5줄 이내로 요약해주세요.

[리포트 내용]
{context[:3000]}

[요약 포인트]
- 투자의견 (매수/중립/매도)
- 목표주가
- 핵심 실적 전망
- 주요 리스크
"""
        response = self.instruct_llm.invoke(summary_prompt)
        return response.content

    def _analyze_charts(self, stock_name: str) -> Tuple[str, int]:
        """차트/그래프 Vision 분석"""
        return "차트 분석은 PaddleOCR-VL이 텍스트로 변환하여 리포트에 포함됨", 0

    def _search_news(self, stock_name: str) -> str:
        """
        최신 뉴스 검색
        Plan A: 웹 뉴스 검색 → Plan B: RAG 폴백

        [최적화] 웹 검색 결과의 LLM 요약을 제거.
        원시 텍스트를 Thinking 모델에 직접 전달하여 1회 호출 절약.
        """
        self._last_news_source = "none"

        # Plan A: 웹 뉴스 검색
        if WEB_SEARCH_AVAILABLE:
            try:
                from src.tools.web_search_tool import search_stock_news
                results = search_stock_news(stock_name, max_results=5)

                if results:
                    self._last_news_source = "web"
                    news_text = "\n".join([
                        f"- [{r.get('title', '')}] {r.get('snippet', '')}"
                        for r in results[:5]
                    ])
                    return news_text
                else:
                    print(f"   ℹ️ 웹 뉴스 결과 없음 → RAG 폴백")
            except Exception as e:
                print(f"   ⚠️ 웹 뉴스 오류: {e} → RAG 폴백")

        # Plan B: RAG 폴백 (source: news, forum)
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
            print(f"   ⚠️ RAG 뉴스 폴백도 실패: {e}")

        return "뉴스를 확보하지 못했습니다. (웹/RAG 모두 실패)"

    def _search_policy(self, stock_name: str) -> str:
        """
        정책/규제 동향 검색
        Plan A: 웹 검색 → Plan B: RAG 폴백
        """
        self._last_policy_source = "none"

        industry_keywords = {
            "삼성전자": "반도체 정책 보조금",
            "SK하이닉스": "반도체 정책 HBM",
            "현대차": "전기차 보조금 정책",
            "LG에너지솔루션": "배터리 IRA 보조금",
            "네이버": "플랫폼 규제 AI",
            "카카오": "플랫폼 규제",
        }
        keyword = industry_keywords.get(stock_name, f"{stock_name} 정책 규제")

        # Plan A: 웹 검색
        if WEB_SEARCH_AVAILABLE:
            try:
                from src.tools.web_search_tool import search_web
                results = search_web(keyword, max_results=3)

                if results:
                    self._last_policy_source = "web"
                    policy_text = "\n".join([
                        f"- {r.get('title', '')}: {r.get('snippet', '')}"
                        for r in results[:3]
                    ])
                    return policy_text[:500]
                else:
                    print(f"   ℹ️ 웹 정책 결과 없음 → RAG 폴백")
            except Exception as e:
                print(f"   ⚠️ 웹 정책 오류: {e} → RAG 폴백")

        # Plan B: RAG 폴백 (source: dart, news, report)
        try:
            rag_query = f"{stock_name} 정책 규제 정부 법안 {keyword}"
            context = self.rag_tool_policy._run(rag_query)

            if not self._is_empty_result(context):
                self._last_policy_source = "rag"
                self._log_retrieval_debug(
                    "policy",
                    rag_query,
                    self._extract_retrieval_hits(context),
                )
                return context[:500] + "\n\n[데이터 출처: RAG 저장 문서]"
        except Exception as e:
            print(f"   ⚠️ RAG 정책 폴백도 실패: {e}")

        return "정책/규제 정보를 확보하지 못했습니다. (웹/RAG 모두 실패)"

    def _search_industry(self, stock_name: str) -> str:
        """
        산업 동향 검색
        Plan A: RAG → Plan B: 웹 검색 폴백
        """
        self._last_industry_source = "none"

        industry_map = {
            "삼성전자": "반도체 메모리 파운드리 시장",
            "SK하이닉스": "HBM AI 반도체 시장",
            "현대차": "전기차 자율주행 시장",
            "LG에너지솔루션": "배터리 전기차 시장",
            "네이버": "검색 AI 클라우드 시장",
            "카카오": "메신저 플랫폼 시장",
            "셀트리온": "바이오시밀러 시장",
        }
        industry_query = industry_map.get(stock_name, f"{stock_name} 산업 동향 시장")

        # Plan A: RAG 검색 (source: report, news)
        try:
            query = f"{stock_name} 산업 동향 시장 전망"
            context = self.rag_tool_industry._run(query)

            if not self._is_empty_result(context):
                self._last_industry_source = "rag"
                self._log_retrieval_debug(
                    "industry",
                    query,
                    self._extract_retrieval_hits(context),
                )
                return context[:500]
            else:
                print(f"   ℹ️ RAG 산업 결과 없음 → 웹 검색 폴백")
        except Exception as e:
            print(f"   ⚠️ RAG 산업 오류: {e} → 웹 검색 폴백")

        # Plan B: 웹 검색 폴백
        if WEB_SEARCH_AVAILABLE:
            try:
                from src.tools.web_search_tool import search_web
                results = search_web(industry_query + " 전망 2024", max_results=3)

                if results:
                    self._last_industry_source = "web"
                    industry_text = "\n".join([
                        f"- {r.get('title', '')}: {r.get('snippet', '')}"
                        for r in results[:3]
                    ])
                    return industry_text[:500] + "\n\n[데이터 출처: 웹 검색]"
            except Exception as e:
                print(f"   ⚠️ 웹 산업 폴백도 실패: {e}")

        return "산업 동향 정보를 확보하지 못했습니다. (RAG/웹 모두 실패)"


# ──────────────────────────────────────────────
# 하위 호환 별칭 (구 코드에서 import 유지)
# ──────────────────────────────────────────────

# 구 researcher.py에서 임포트하던 코드 호환
ResearcherAgent = AnalystAgent

# 구 strategist.py에서 임포트하던 코드 호환
StrategistAgent = AnalystAgent


# 사용 예시
if __name__ == "__main__":
    agent = AnalystAgent()

    print("=" * 60)
    print("삼성전자 헤게모니 분석 (통합 Analyst Agent)")
    print("=" * 60)

    score = agent.full_analysis("삼성전자", "005930")

    print(f"\n📊 분석 결과:")
    print(f"   헤게모니 등급: {score.hegemony_grade}")
    print(f"   독점력: {score.moat_score}/40점")
    print(f"   성장성: {score.growth_score}/30점")
    print(f"   총점: {score.total_score}/70점")
    print(f"\n💡 총평: {score.final_opinion}")
    print(f"\n🛡️ 경쟁 우위: {score.competitive_advantage}")
    print(f"⚠️ 리스크: {score.risk_factors}")
