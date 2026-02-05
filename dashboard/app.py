# 파일: dashboard/app.py
"""
HQA 대시보드 - Streamlit 기반 웹 UI

실행:
    cd HQA_Project
    streamlit run dashboard/app.py

기능:
- 종목 검색 및 선택
- 실시간 시세 조회
- AI 에이전트 분석 실행
- 분석 결과 시각화
- 대화형 질문 (Supervisor)
"""

import streamlit as st
import pandas as pd
import sys
from pathlib import Path
from datetime import datetime

# 프로젝트 루트를 path에 추가
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ==========================================
# 페이지 설정
# ==========================================
st.set_page_config(
    page_title="HQA - AI 주식 분석",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 커스텀 CSS
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: bold;
        color: #1E88E5;
        text-align: center;
        padding: 1rem 0;
    }
    .sub-header {
        font-size: 1.2rem;
        color: #666;
        text-align: center;
        margin-bottom: 2rem;
    }
    .metric-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1rem;
        border-radius: 10px;
        color: white;
    }
    .score-good { color: #4CAF50; font-weight: bold; }
    .score-neutral { color: #FF9800; font-weight: bold; }
    .score-bad { color: #F44336; font-weight: bold; }
    .stTabs [data-baseweb="tab-list"] {
        gap: 24px;
    }
</style>
""", unsafe_allow_html=True)


# ==========================================
# 세션 상태 초기화
# ==========================================
if "analysis_result" not in st.session_state:
    st.session_state.analysis_result = None
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "selected_stock" not in st.session_state:
    st.session_state.selected_stock = None


# ==========================================
# 유틸리티 함수
# ==========================================
@st.cache_resource
def get_stock_mapper():
    """종목 매퍼 로드 (캐시)"""
    try:
        from src.utils.stock_mapper import get_mapper
        return get_mapper()
    except Exception as e:
        st.error(f"종목 매퍼 로드 실패: {e}")
        return None


@st.cache_resource
def get_realtime_tool():
    """실시간 시세 도구 로드"""
    try:
        from src.tools.realtime_tool import KISRealtimeTool
        return KISRealtimeTool()
    except Exception as e:
        st.warning(f"실시간 시세 도구 로드 실패: {e}")
        return None


def get_supervisor():
    """Supervisor 에이전트 로드"""
    try:
        from src.agents import SupervisorAgent
        return SupervisorAgent()
    except Exception as e:
        st.error(f"Supervisor 로드 실패: {e}")
        return None


def format_number(num):
    """숫자 포맷팅"""
    if num is None:
        return "-"
    if abs(num) >= 1_000_000_000_000:
        return f"{num/1_000_000_000_000:.1f}조"
    elif abs(num) >= 100_000_000:
        return f"{num/100_000_000:.1f}억"
    elif abs(num) >= 10_000:
        return f"{num/10_000:.1f}만"
    else:
        return f"{num:,.0f}"


def get_score_class(score, max_score):
    """점수에 따른 CSS 클래스 반환"""
    ratio = score / max_score if max_score > 0 else 0
    if ratio >= 0.7:
        return "score-good"
    elif ratio >= 0.4:
        return "score-neutral"
    else:
        return "score-bad"


# ==========================================
# 사이드바
# ==========================================
def render_sidebar():
    """사이드바 렌더링"""
    with st.sidebar:
        st.markdown("## 🎯 HQA")
        st.markdown("**AI 멀티 에이전트 주식 분석**")
        st.markdown("---")
        
        # 종목 검색
        st.markdown("### 📌 종목 선택")
        
        mapper = get_stock_mapper()
        
        # 검색 입력
        search_input = st.text_input(
            "종목명 또는 코드",
            placeholder="예: 삼성전자, 005930",
            key="stock_search"
        )
        
        # 빠른 선택 버튼
        st.markdown("**인기 종목:**")
        col1, col2 = st.columns(2)
        
        popular_stocks = [
            ("삼성전자", "005930"),
            ("SK하이닉스", "000660"),
            ("NAVER", "035420"),
            ("카카오", "035720"),
            ("LG에너지솔루션", "373220"),
            ("삼성바이오로직스", "207940"),
        ]
        
        for i, (name, code) in enumerate(popular_stocks):
            col = col1 if i % 2 == 0 else col2
            if col.button(name, key=f"btn_{code}", use_container_width=True):
                st.session_state.selected_stock = {"name": name, "code": code}
                st.rerun()
        
        # 검색 결과
        if search_input and mapper:
            if search_input.isdigit() and len(search_input) == 6:
                name = mapper.get_name(search_input)
                if name:
                    st.session_state.selected_stock = {"name": name, "code": search_input}
            else:
                results = mapper.search(search_input)
                if results:
                    for r in results[:5]:
                        if st.button(f"{r['name']} ({r['code']})", key=f"search_{r['code']}"):
                            st.session_state.selected_stock = r
                            st.rerun()
        
        st.markdown("---")
        
        # 현재 선택된 종목
        if st.session_state.selected_stock:
            stock = st.session_state.selected_stock
            st.success(f"✅ 선택: **{stock['name']}** ({stock['code']})")
        
        st.markdown("---")
        
        # 설정
        st.markdown("### ⚙️ 설정")
        analysis_mode = st.radio(
            "분석 모드",
            ["전체 분석", "빠른 분석"],
            help="전체 분석: Analyst + Quant + Chartist + Risk Manager\n빠른 분석: Quant + Chartist"
        )
        
        return analysis_mode


# ==========================================
# 메인 콘텐츠 - 실시간 시세
# ==========================================
def render_realtime_price():
    """실시간 시세 표시"""
    if not st.session_state.selected_stock:
        st.info("👈 사이드바에서 종목을 선택하세요.")
        return
    
    stock = st.session_state.selected_stock
    tool = get_realtime_tool()
    
    st.markdown(f"## 📊 {stock['name']} ({stock['code']})")
    
    if tool and tool.is_available:
        with st.spinner("시세 조회 중..."):
            price = tool.get_current_price(stock['code'])
        
        if price:
            # 메트릭 표시
            col1, col2, col3, col4 = st.columns(4)
            
            change_color = "normal" if price.change == 0 else ("inverse" if price.change < 0 else "off")
            
            col1.metric(
                "현재가",
                f"{price.current_price:,}원",
                f"{price.change:+,}원 ({price.change_rate:+.2f}%)",
                delta_color=change_color
            )
            col2.metric("시가", f"{price.open_price:,}원")
            col3.metric("고가", f"{price.high_price:,}원")
            col4.metric("저가", f"{price.low_price:,}원")
            
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("거래량", format_number(price.volume))
            col2.metric("시가총액", f"{price.market_cap:,}억원")
            col3.metric("PER", f"{price.per:.2f}")
            col4.metric("PBR", f"{price.pbr:.2f}")
            
            st.caption(f"조회시간: {price.timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            st.warning("시세 정보를 가져올 수 없습니다.")
    else:
        st.warning("⚠️ 실시간 시세 API가 설정되지 않았습니다.")
        st.info("`.env` 파일에 `KIS_APP_KEY`, `KIS_APP_SECRET`을 설정하세요.")
        
        # 더미 데이터 표시
        st.markdown("**[데모 데이터]**")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("현재가", "55,000원", "+500원 (+0.92%)")
        col2.metric("시가", "54,800원")
        col3.metric("고가", "55,200원")
        col4.metric("저가", "54,500원")


# ==========================================
# 메인 콘텐츠 - AI 분석
# ==========================================
def render_analysis(analysis_mode: str):
    """AI 분석 실행 및 결과 표시"""
    if not st.session_state.selected_stock:
        st.info("👈 사이드바에서 종목을 선택하세요.")
        return
    
    stock = st.session_state.selected_stock
    
    st.markdown(f"## 🤖 AI 분석: {stock['name']}")
    
    # 분석 실행 버튼
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        if st.button("🚀 분석 시작", use_container_width=True, type="primary"):
            run_analysis(stock, analysis_mode)
    
    # 분석 결과 표시
    if st.session_state.analysis_result:
        display_analysis_result(st.session_state.analysis_result)


def run_analysis(stock: dict, mode: str):
    """분석 실행"""
    progress = st.progress(0)
    status = st.empty()
    
    try:
        if mode == "빠른 분석":
            # 빠른 분석
            from src.agents import QuantAgent, ChartistAgent
            
            status.text("📈 Quant 분석 중...")
            progress.progress(20)
            quant = QuantAgent()
            quant_score = quant.full_analysis(stock['name'], stock['code'])
            
            status.text("📉 Chartist 분석 중...")
            progress.progress(60)
            chartist = ChartistAgent()
            chartist_score = chartist.full_analysis(stock['name'], stock['code'])
            
            progress.progress(100)
            status.text("✅ 분석 완료!")
            
            st.session_state.analysis_result = {
                "mode": "quick",
                "stock": stock,
                "quant": quant_score,
                "chartist": chartist_score,
                "timestamp": datetime.now()
            }
            
        else:
            # 전체 분석
            from src.agents import (
                AnalystAgent, QuantAgent, ChartistAgent,
                RiskManagerAgent, AgentScores
            )
            
            status.text("🔍 Analyst 분석 중 (헤게모니)...")
            progress.progress(10)
            analyst = AnalystAgent()
            analyst_score = analyst.full_analysis(stock['name'], stock['code'])
            
            status.text("📈 Quant 분석 중 (재무)...")
            progress.progress(35)
            quant = QuantAgent()
            quant_score = quant.full_analysis(stock['name'], stock['code'])
            
            status.text("📉 Chartist 분석 중 (기술적)...")
            progress.progress(60)
            chartist = ChartistAgent()
            chartist_score = chartist.full_analysis(stock['name'], stock['code'])
            
            status.text("🎯 Risk Manager 최종 판단 중...")
            progress.progress(80)
            risk_manager = RiskManagerAgent()
            
            agent_scores = AgentScores(
                analyst_moat_score=analyst_score.moat_score,
                analyst_growth_score=analyst_score.growth_score,
                analyst_total=analyst_score.total_score,
                analyst_grade=analyst_score.hegemony_grade,
                analyst_opinion=analyst_score.final_opinion,
                quant_valuation_score=quant_score.valuation_score,
                quant_profitability_score=quant_score.profitability_score,
                quant_growth_score=quant_score.growth_score,
                quant_stability_score=quant_score.stability_score,
                quant_total=quant_score.total_score,
                quant_opinion=quant_score.opinion,
                chartist_trend_score=chartist_score.trend_score,
                chartist_momentum_score=chartist_score.momentum_score,
                chartist_volatility_score=chartist_score.volatility_score,
                chartist_volume_score=chartist_score.volume_score,
                chartist_total=chartist_score.total_score,
                chartist_signal=chartist_score.signal
            )
            
            final = risk_manager.make_decision(stock['name'], stock['code'], agent_scores)
            
            progress.progress(100)
            status.text("✅ 분석 완료!")
            
            st.session_state.analysis_result = {
                "mode": "full",
                "stock": stock,
                "analyst": analyst_score,
                "quant": quant_score,
                "chartist": chartist_score,
                "final": final,
                "timestamp": datetime.now()
            }
        
        st.rerun()
        
    except Exception as e:
        st.error(f"분석 중 오류 발생: {e}")
        progress.empty()
        status.empty()


def display_analysis_result(result: dict):
    """분석 결과 표시"""
    st.markdown("---")
    
    if result["mode"] == "quick":
        # 빠른 분석 결과
        col1, col2 = st.columns(2)
        
        with col1:
            st.markdown("### 📈 Quant (재무)")
            quant = result["quant"]
            st.metric("총점", f"{quant.total_score}/100", quant.grade)
            
            scores = {
                "밸류에이션": (quant.valuation_score, 25),
                "수익성": (quant.profitability_score, 25),
                "성장성": (quant.growth_score, 25),
                "안정성": (quant.stability_score, 25),
            }
            
            for name, (score, max_s) in scores.items():
                st.progress(score / max_s, text=f"{name}: {score}/{max_s}")
        
        with col2:
            st.markdown("### 📉 Chartist (기술적)")
            chartist = result["chartist"]
            st.metric("총점", f"{chartist.total_score}/100", chartist.signal)
            
            scores = {
                "추세": (chartist.trend_score, 30),
                "모멘텀": (chartist.momentum_score, 30),
                "변동성": (chartist.volatility_score, 20),
                "거래량": (chartist.volume_score, 20),
            }
            
            for name, (score, max_s) in scores.items():
                st.progress(score / max_s, text=f"{name}: {score}/{max_s}")
        
        # 종합 의견
        avg = (result["quant"].total_score + result["chartist"].total_score) / 2
        if avg >= 70:
            st.success(f"🎯 **빠른 판단**: 긍정적 - 매수 고려 (평균 {avg:.0f}점)")
        elif avg >= 50:
            st.warning(f"🎯 **빠른 판단**: 중립 - 관망 권고 (평균 {avg:.0f}점)")
        else:
            st.error(f"🎯 **빠른 판단**: 부정적 - 신중한 접근 필요 (평균 {avg:.0f}점)")
    
    else:
        # 전체 분석 결과
        tabs = st.tabs(["📜 최종 판단", "🔍 Analyst", "📈 Quant", "📉 Chartist"])
        
        with tabs[0]:
            final = result["final"]
            
            # 최종 투자 의견
            action_colors = {
                "적극 매수": "🟢", "매수": "🟢",
                "보유/관망": "🟡",
                "비중 축소": "🟠", "매도": "🔴", "적극 매도": "🔴"
            }
            color = action_colors.get(final.action.value, "⚪")
            
            st.markdown(f"## {color} {final.action.value}")
            st.markdown(f"**확신도**: {final.confidence}% | **리스크**: {final.risk_level.value}")
            st.markdown(f"**종합 점수**: {final.total_score}/100")
            
            st.markdown("### 💡 요약")
            st.info(final.summary)
            
            col1, col2 = st.columns(2)
            with col1:
                st.markdown("**📈 핵심 촉매**")
                for catalyst in final.key_catalysts:
                    st.markdown(f"- {catalyst}")
            
            with col2:
                st.markdown("**⚠️ 리스크 요인**")
                for risk in final.risk_factors:
                    st.markdown(f"- {risk}")
            
            with st.expander("상세 추론 보기"):
                st.markdown(final.detailed_reasoning)
        
        with tabs[1]:
            analyst = result["analyst"]
            st.markdown(f"### 헤게모니 등급: **{analyst.hegemony_grade}**")
            st.metric("총점", f"{analyst.total_score}/70")
            
            col1, col2 = st.columns(2)
            col1.metric("독점력 (Moat)", f"{analyst.moat_score}/40")
            col2.metric("성장성", f"{analyst.growth_score}/30")
            
            st.markdown("**최종 의견**")
            st.info(analyst.final_opinion)
            
            with st.expander("상세 분석"):
                st.markdown(f"**독점력 분석**: {analyst.moat_reason}")
                st.markdown(f"**성장성 분석**: {analyst.growth_reason}")
        
        with tabs[2]:
            quant = result["quant"]
            st.markdown(f"### 등급: **{quant.grade}**")
            st.metric("총점", f"{quant.total_score}/100")
            
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("밸류에이션", f"{quant.valuation_score}/25")
            col2.metric("수익성", f"{quant.profitability_score}/25")
            col3.metric("성장성", f"{quant.growth_score}/25")
            col4.metric("안정성", f"{quant.stability_score}/25")
            
            st.info(quant.opinion)
        
        with tabs[3]:
            chartist = result["chartist"]
            st.markdown(f"### 신호: **{chartist.signal}**")
            st.metric("총점", f"{chartist.total_score}/100")
            
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("추세", f"{chartist.trend_score}/30")
            col2.metric("모멘텀", f"{chartist.momentum_score}/30")
            col3.metric("변동성", f"{chartist.volatility_score}/20")
            col4.metric("거래량", f"{chartist.volume_score}/20")
    
    st.caption(f"분석 시간: {result['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}")


# ==========================================
# 메인 콘텐츠 - 대화형 질문
# ==========================================
def render_chat():
    """대화형 질문 인터페이스"""
    st.markdown("## 💬 AI에게 질문하기")
    
    # 채팅 히스토리 표시
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    
    # 입력
    if prompt := st.chat_input("예: 삼성전자 분석해줘, 반도체 산업 동향은?"):
        # 사용자 메시지 추가
        st.session_state.chat_history.append({"role": "user", "content": prompt})
        
        with st.chat_message("user"):
            st.markdown(prompt)
        
        # AI 응답
        with st.chat_message("assistant"):
            with st.spinner("생각 중..."):
                supervisor = get_supervisor()
                if supervisor:
                    try:
                        response = supervisor.execute(prompt)
                        st.markdown(response)
                        st.session_state.chat_history.append({"role": "assistant", "content": response})
                    except Exception as e:
                        error_msg = f"오류 발생: {e}"
                        st.error(error_msg)
                        st.session_state.chat_history.append({"role": "assistant", "content": error_msg})
                else:
                    st.error("Supervisor 에이전트를 로드할 수 없습니다.")


# ==========================================
# 메인 함수
# ==========================================
def main():
    # 헤더
    st.markdown('<p class="main-header">📈 HQA</p>', unsafe_allow_html=True)
    st.markdown('<p class="sub-header">Hegemony Quantitative Analyst - AI 멀티 에이전트 주식 분석</p>', unsafe_allow_html=True)
    
    # 사이드바
    analysis_mode = render_sidebar()
    
    # 메인 탭
    tab1, tab2, tab3 = st.tabs(["📊 시세", "🤖 AI 분석", "💬 대화"])
    
    with tab1:
        render_realtime_price()
    
    with tab2:
        render_analysis(analysis_mode)
    
    with tab3:
        render_chat()
    
    # 푸터
    st.markdown("---")
    st.markdown(
        "<div style='text-align: center; color: #888;'>"
        "HQA v0.1.0 | Powered by Gemini AI | "
        "⚠️ 본 분석은 투자 권유가 아닙니다. 투자 책임은 본인에게 있습니다."
        "</div>",
        unsafe_allow_html=True
    )


if __name__ == "__main__":
    main()
