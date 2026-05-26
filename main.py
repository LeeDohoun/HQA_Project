# 파일: main.py
"""
HQA (Hegemony Quantitative Analyst) 메인 실행 파일

실행 모드:
1. Interactive Mode: 자연어 쿼리 입력
2. Single Stock Analysis: 특정 종목 전체 분석
3. Quick Analysis: 빠른 분석 (Thinking 없음)
4. Realtime Price: 실시간 시세 조회
5. Autonomous Mode: 설정 기반 자동 분석 + 매매

사용법:
    python main.py                    # 대화형 모드
    python main.py --stock 삼성전자    # 종목 분석
    python main.py --quick 005930     # 빠른 분석
    python main.py --theme 2차전지    # 테마 주도주 선정
    python main.py --price 005930     # 실시간 시세
    python main.py --auto             # 자율 에이전트 (1회 실행)
    python main.py --auto --loop      # 자율 에이전트 (반복 실행)
    python main.py --auto --dry-run   # 매매 시뮬레이션
"""

import argparse
import sys
from typing import Optional

# ==========================================
# 지연 임포트 함수들 (시작 속도 향상)
# ==========================================

def get_supervisor():
    """Supervisor 에이전트 로드"""
    from src.agents import SupervisorAgent
    return SupervisorAgent()


def get_realtime_tool():
    """실시간 시세 도구 로드"""
    from src.tools.realtime_tool import KISRealtimeTool
    return KISRealtimeTool()


def get_stock_mapper():
    """종목 매퍼 로드"""
    from src.utils.stock_mapper import get_mapper
    return get_mapper()


# ==========================================
# 메인 분석 함수들
# ==========================================

def run_interactive_mode():
    """
    대화형 모드 - 자연어로 질문하면 분석
    
    Example:
        > 삼성전자 분석해줘
        > SK하이닉스 현재가 알려줘
        > 반도체 산업 동향은?
        > 삼성전자랑 SK하이닉스 비교해줘
    """
    print("=" * 60)
    print("🚀 HQA (Hegemony Quantitative Analyst)")
    print("   AI 기반 멀티 에이전트 주식 분석 시스템")
    print("=" * 60)
    print("\n💡 사용 예시:")
    print("   - '삼성전자 분석해줘'")
    print("   - '005930 현재가'")
    print("   - '반도체 산업 동향 분석'")
    print("   - '삼성전자 SK하이닉스 비교'")
    print("   - 'exit' 또는 'quit'로 종료\n")
    
    supervisor = get_supervisor()
    
    while True:
        try:
            query = input("🔍 질문> ").strip()
            
            if not query:
                continue
            
            if query.lower() in ["exit", "quit", "q", "종료"]:
                print("\n👋 HQA를 종료합니다.")
                break
            
            print("\n" + "-" * 50)
            
            # Supervisor가 쿼리 분석 및 실행
            result = supervisor.execute(query)
            
            print("\n" + "=" * 50)
            print(format_result(result))
            print("=" * 50 + "\n")
            
        except KeyboardInterrupt:
            print("\n\n👋 HQA를 종료합니다.")
            break
        except Exception as e:
            print(f"\n❌ 오류 발생: {e}\n")


def run_stock_analysis(stock_input: str, quick: bool = False):
    """
    종목 분석 실행
    
    Args:
        stock_input: 종목명 또는 종목코드
        quick: True면 빠른 분석 (Thinking 없음)
    """
    # 종목 매핑
    mapper = get_stock_mapper()
    
    # 종목코드인지 종목명인지 판단
    if stock_input.isdigit() and len(stock_input) == 6:
        stock_code = stock_input
        stock_name = mapper.get_name(stock_code) or stock_input
    else:
        stock_code = mapper.get_code(stock_input)
        stock_name = stock_input
        
        if not stock_code:
            print(f"❌ '{stock_input}' 종목을 찾을 수 없습니다.")
            return None
    
    print("=" * 60)
    if quick:
        print(f"⚡ [Quick Analysis] {stock_name}({stock_code})")
    else:
        print(f"🚀 [Full Analysis] {stock_name}({stock_code})")
    print("=" * 60)
    
    if quick:
        return _run_quick_analysis(stock_code, stock_name)
    else:
        return _run_full_analysis(stock_code, stock_name)


def _run_full_analysis(stock_code: str, stock_name: str):
    """전체 분석 (LangGraph 워크플로우 우선, 폴백: 병렬 실행)"""
    from src.agents.graph import run_stock_analysis, is_langgraph_available
    from src.agents import RiskManagerAgent
    
    if is_langgraph_available():
        print(f"\n⚡ LangGraph 워크플로우로 분석 실행")
    else:
        print(f"\n⚡ Phase 1: Analyst + Quant + Chartist 병렬 실행")
    print("-" * 50)
    
    result = run_stock_analysis(
        stock_name=stock_name,
        stock_code=stock_code,
        max_retries=1,
    )
    
    scores = result.get("scores", {})
    analyst_score = scores.get("analyst")
    quant_score = scores.get("quant")
    chartist_score = scores.get("chartist")
    final_decision = result.get("final_decision")
    
    if analyst_score:
        print(f"   → Analyst  헤게모니: {analyst_score.hegemony_grade} ({analyst_score.total_score}/70점)")
    if quant_score:
        print(f"   → Quant    재무등급: {quant_score.grade} ({quant_score.total_score}/100점)")
    if chartist_score:
        print(f"   → Chartist 기술신호: {chartist_score.signal} ({chartist_score.total_score}/100점)")
    
    # 최종 보고서
    if final_decision:
        print("\n" + "=" * 60)
        print("📜 [최종 투자 판단]")
        print("=" * 60)
        risk_manager = RiskManagerAgent()
        report = risk_manager.generate_report(final_decision)
        print(report)
    
    return final_decision


def _run_quick_analysis(stock_code: str, stock_name: str):
    """빠른 분석 (Thinking 없음)"""
    from src.agents import QuantAgent, ChartistAgent
    
    quant = QuantAgent()
    chartist = ChartistAgent()
    
    # Quant
    print(f"\n📈 Quant 분석...")
    quant_score = quant.full_analysis(stock_name, stock_code)
    print(f"   → {quant_score.total_score}/100점 ({quant_score.grade})")
    
    # Chartist
    print(f"\n📉 Chartist 분석...")
    chartist_score = chartist.full_analysis(stock_name, stock_code)
    print(f"   → {chartist_score.total_score}/100점 ({chartist_score.signal})")
    
    # 간단 종합
    avg_score = (quant_score.total_score + chartist_score.total_score) / 2
    
    if avg_score >= 70:
        opinion = "긍정적 - 매수 고려"
    elif avg_score >= 50:
        opinion = "중립 - 관망 권고"
    else:
        opinion = "부정적 - 신중한 접근 필요"
    
    print(f"\n🎯 빠른 판단: {opinion} (평균 {avg_score:.0f}점)")
    
    return {
        "quant": quant_score,
        "chartist": chartist_score,
        "opinion": opinion,
        "avg_score": avg_score
    }


def show_realtime_price(stock_input: str):
    """
    실시간 시세 조회
    
    Args:
        stock_input: 종목명 또는 종목코드
    """
    mapper = get_stock_mapper()
    
    # 종목코드 변환
    if stock_input.isdigit() and len(stock_input) == 6:
        stock_code = stock_input
    else:
        stock_code = mapper.get_code(stock_input)
        if not stock_code:
            print(f"❌ '{stock_input}' 종목을 찾을 수 없습니다.")
            return
    
    tool = get_realtime_tool()
    
    if not tool.is_available:
        print("❌ KIS API가 설정되지 않았습니다.")
        print("   .env 파일에 KIS_APP_KEY, KIS_APP_SECRET을 설정하세요.")
        return
    
    print(tool.get_quote_summary(stock_code))


def run_theme_orchestration(
    theme: str,
    theme_key: str = "",
    candidate_limit: int = 5,
    top_n: int = 3,
):
    """테마 데이터 기반 주도주 오케스트레이션 실행"""
    from src.agents import ThemeLeaderOrchestrator

    print("=" * 60)
    print(f"🏁 [Theme Orchestration] {theme}")
    print("=" * 60)
    print(f"   후보 추출: 최대 {candidate_limit}개")
    print(f"   최종 선정: 상위 {top_n}개")
    print("-" * 60)

    orchestrator = ThemeLeaderOrchestrator()
    result = orchestrator.run(
        theme=theme,
        theme_key=theme_key,
        candidate_limit=candidate_limit,
        top_n=top_n,
    )

    if result.get("status") != "success":
        print(f"❌ {result.get('message', '테마 오케스트레이션 실패')}")
        return result

    print("\n🏆 [주도주 결과]")
    print(result.get("summary", "요약 없음"))

    leaders = result.get("leaders", [])
    for idx, leader in enumerate(leaders, start=1):
        candidate = leader.get("candidate", {})
        decision = leader.get("final_decision", {})
        print("\n" + "=" * 60)
        print(
            f"{idx}. {candidate.get('stock_name', 'N/A')}({candidate.get('stock_code', 'N/A')})"
        )
        print("=" * 60)
        print(f"리더 점수: {leader.get('leader_score', 0)}")
        print(f"최종 판단: {decision.get('action', 'N/A')}")
        print(f"확신도: {decision.get('confidence', 0)}%")
        print(f"요약: {decision.get('summary', '')}")

    return result


def format_result(result):
    """CLI 출력용 결과 포맷터"""
    if not isinstance(result, dict):
        return str(result)

    if result.get("status") == "success" and result.get("leaders"):
        lines = [f"🏁 {result.get('theme', '테마')} 주도주 선정 결과"]
        if result.get("summary"):
            lines.extend(["", result["summary"]])
        for idx, leader in enumerate(result.get("leaders", []), start=1):
            candidate = leader.get("candidate", {})
            decision = leader.get("final_decision", {})
            lines.extend(
                [
                    "",
                    f"{idx}. {candidate.get('stock_name', 'N/A')}({candidate.get('stock_code', 'N/A')})",
                    f"   리더 점수: {leader.get('leader_score', 0)}",
                    f"   최종 판단: {decision.get('action', 'N/A')} / 확신도 {decision.get('confidence', 0)}%",
                    f"   요약: {decision.get('summary', '')}",
                ]
            )
        return "\n".join(lines)

    if result.get("summary"):
        return result["summary"]

    if result.get("answer"):
        return result["answer"]

    if result.get("analysis"):
        return result["analysis"]

    return str(result)


def show_help():
    """도움말 출력"""
    help_text = """
═══════════════════════════════════════════════════════════════
  HQA (Hegemony Quantitative Analyst) - 사용법
═══════════════════════════════════════════════════════════════

📌 실행 모드:

  1. 대화형 모드 (기본)
     python main.py
     
  2. 종목 분석 (전체)
     python main.py --stock 삼성전자
     python main.py --stock 005930
     python main.py -s SK하이닉스
     
  3. 빠른 분석 (Quant + Chartist만)
     python main.py --quick 삼성전자
     python main.py -q 005930
     
  4. 실시간 시세
     python main.py --price 삼성전자
     python main.py -p 005930

  5. 테마 주도주 선정
     python main.py --theme 2차전지
     python main.py --theme 반도체 --candidate-limit 7 --top-n 3

  6. 자율 에이전트 모드
     python main.py --auto             # 감시 목록 1회 분석
     python main.py --auto --loop      # 스케줄 반복 실행
     python main.py --auto --dry-run   # 매매 시뮬레이션만
     python main.py --auto --config config/watchlist.yaml

═══════════════════════════════════════════════════════════════

📌 대화형 모드 질문 예시:

  - "삼성전자 분석해줘"
  - "005930 현재가 알려줘"  
  - "반도체 산업 동향"
  - "삼성전자 SK하이닉스 비교"
  - "AI 관련 종목 추천"

═══════════════════════════════════════════════════════════════

📌 필수 설정 (.env 파일):

  GOOGLE_API_KEY=your_gemini_api_key     # 필수
  KIS_APP_KEY=your_kis_app_key           # 실시간 시세용
  KIS_APP_SECRET=your_kis_app_secret     # 실시간 시세용
  DART_API_KEY=your_dart_api_key         # 공시 조회용 (선택)

═══════════════════════════════════════════════════════════════
"""
    print(help_text)


# ==========================================
# 자율 에이전트 모드
# ==========================================

def run_autonomous_mode(
    config_path: str = "config/watchlist.yaml",
    loop: bool = False,
    dry_run: bool = False,
):
    """
    자율 에이전트 모드 — 설정 기반 자동 분석 + 매매

    Args:
        config_path: YAML 설정 파일 경로
        loop: True면 스케줄 반복 실행
        dry_run: True면 매매 시뮬레이션
    """
    from src.runner.autonomous_runner import AutonomousRunner

    runner = AutonomousRunner(
        config_path=config_path,
        dry_run_override=True if dry_run else None,
    )

    if loop:
        runner.run_loop()
    else:
        runner.run_once()


def run_theme_trading_mode(
    *,
    theme: str,
    theme_key: str = "",
    candidate_limit: int = 5,
    top_n: int = 3,
    execute_top_n: int = 1,
    execute: bool = False,
    min_leader_score: Optional[int] = None,
    strategy_profile: str = "default",
    config_path: str = "config/watchlist.yaml",
    paper: bool = False,
    dry_run: bool = False,
):
    """테마 주도주를 발굴한 뒤 기존 TradeExecutor 경로로 preview/execute."""
    from src.runner import ThemeLeaderTradingRunner

    if execute and not (paper or dry_run):
        raise ValueError("--theme-trade --execute requires --paper or --dry-run")

    dry_run_override = True
    trading_enabled_override = True
    account_type_override = "paper"
    if dry_run:
        dry_run_override = True
        trading_enabled_override = True
    elif paper and execute:
        dry_run_override = False
        trading_enabled_override = True

    runner = ThemeLeaderTradingRunner(
        config_path=config_path,
        dry_run_override=dry_run_override,
        trading_enabled_override=trading_enabled_override,
        account_type_override=account_type_override,
    )
    result = runner.run_once(
        theme=theme,
        theme_key=theme_key,
        candidate_limit=candidate_limit,
        top_n=top_n,
        execute_top_n=execute_top_n,
        execute=execute,
        min_leader_score=min_leader_score,
        strategy_profile=strategy_profile,
    )

    mode_label = "실행" if execute else "미리보기"
    print("=" * 60)
    print(f"🏁 [Theme Leader Trading {mode_label}] {theme}")
    print("=" * 60)
    print(f"   후보 평가: {result.get('evaluated_count', 0)}개")
    print(f"   거래 대상: {result.get('selected_count', 0)}개")
    print(f"   결과 요약: {result.get('summary', {})}")
    if result.get("report_path"):
        print(f"   리포트: {result['report_path']}")

    for row in result.get("trade_results", []):
        print(
            f"   - #{row.get('rank')} {row.get('stock_name')}({row.get('stock_code')}): "
            f"{row.get('status')} price={row.get('price')}"
        )
        detail = row.get("trade") or row.get("preview") or {}
        reason = detail.get("reason") or row.get("reason")
        if reason:
            print(f"     reason: {reason}")

    return result


def run_theme_report_trading_mode(
    *,
    report_path: str,
    execute_top_n: int = 1,
    execute: bool = False,
    config_path: str = "config/watchlist.yaml",
    paper: bool = False,
    dry_run: bool = False,
):
    """저장된 theme-trade preview 리포트를 재평가 없이 preview/execute."""
    from src.runner import ThemeLeaderTradingRunner

    if execute and not (paper or dry_run):
        raise ValueError("--theme-trade-report --execute requires --paper or --dry-run")

    dry_run_override = True
    trading_enabled_override = True
    account_type_override = "paper"
    if paper and execute:
        dry_run_override = False

    runner = ThemeLeaderTradingRunner(
        config_path=config_path,
        dry_run_override=dry_run_override,
        trading_enabled_override=trading_enabled_override,
        account_type_override=account_type_override,
    )
    result = runner.run_from_report(
        report_path=report_path,
        execute_top_n=execute_top_n,
        execute=execute,
    )

    mode_label = "리포트 실행" if execute else "리포트 미리보기"
    print("=" * 60)
    print(f"🏁 [Theme Leader Trading {mode_label}]")
    print("=" * 60)
    print(f"   원본 리포트: {report_path}")
    print(f"   거래 대상: {result.get('selected_count', 0)}개")
    print(f"   결과 요약: {result.get('summary', {})}")
    if result.get("report_path"):
        print(f"   리포트: {result['report_path']}")

    for row in result.get("trade_results", []):
        print(
            f"   - #{row.get('rank')} {row.get('stock_name')}({row.get('stock_code')}): "
            f"{row.get('status')} price={row.get('price')}"
        )
        detail = row.get("trade") or row.get("preview") or {}
        reason = detail.get("reason") or row.get("reason")
        if reason:
            print(f"     reason: {reason}")

    return result


def run_multi_theme_trading_mode(
    *,
    top_n: int = 3,
    per_theme_top_n: int = 3,
    candidate_limit: int = 5,
    execute: bool = False,
    min_leader_score: Optional[int] = None,
    min_confidence: Optional[int] = None,
    max_risk_level: Optional[str] = None,
    strategy_profile: str = "default",
    config_path: str = "config/watchlist.yaml",
    paper: bool = False,
    dry_run: bool = False,
):
    """전체 테마 순회 후 통합 랭킹 기반으로 상위 주도주를 preview/execute."""
    from src.runner import MultiThemeLeaderTradingRunner

    if execute and not (paper or dry_run):
        raise ValueError("--multi-theme-trade --execute requires --paper or --dry-run")

    dry_run_override = True
    trading_enabled_override = True
    account_type_override = "paper"
    if paper and execute:
        dry_run_override = False

    runner = MultiThemeLeaderTradingRunner(
        config_path=config_path,
        dry_run_override=dry_run_override,
        trading_enabled_override=trading_enabled_override,
        account_type_override=account_type_override,
    )

    result = runner.run_all(
        candidate_limit=candidate_limit,
        per_theme_top_n=per_theme_top_n,
        top_n=top_n,
        execute=execute,
        min_leader_score=min_leader_score,
        min_confidence=min_confidence,
        max_risk_level=max_risk_level,
        strategy_profile=strategy_profile,
        buy_only=True,
    )

    mode_label = "실행" if execute else "미리보기"
    print("=" * 60)
    print(f"🏁 [Multi Theme Leader Trading {mode_label}]")
    print("=" * 60)
    print(f"   테마 수: {result.get('theme_count', 0)}")
    print(f"   후보 리더 수: {result.get('leader_count', 0)}")
    print(f"   최종 선별 수: {result.get('selected_count', 0)}")
    print(f"   전략 프로필: {result.get('strategy_profile', strategy_profile)}")
    print(f"   best_theme: {result.get('best_theme')}")
    print(f"   결과 요약: {result.get('summary', {})}")
    if result.get("report_path"):
        print(f"   리포트: {result['report_path']}")

    for row in result.get("trade_results", []):
        print(
            f"   - #{row.get('global_rank')} {row.get('stock_name')}({row.get('stock_code')}) "
            f"[{row.get('theme_key')}] : {row.get('status')} price={row.get('price')}"
        )
        detail = row.get("trade") or row.get("preview") or {}
        reason = detail.get("reason") or row.get("reason")
        if reason:
            print(f"     reason: {reason}")

    return result


def run_multi_theme_trading_loop_mode(
    *,
    top_n: int = 3,
    per_theme_top_n: int = 3,
    candidate_limit: int = 5,
    execute: bool = False,
    min_leader_score: Optional[int] = None,
    min_confidence: Optional[int] = None,
    max_risk_level: Optional[str] = None,
    strategy_profile: str = "short",
    config_path: str = "config/watchlist.yaml",
    paper: bool = False,
    dry_run: bool = False,
    trade_interval_minutes: int = 60,
    market_hours_only: bool = True,
    long_plan_time: str = "08:00",
    long_plan_window_minutes: int = 40,
    long_trigger_check_minutes: int = 5,
    long_market_hours_only: bool = True,
    collect_interval_minutes: Optional[int] = None,
    collect_command: Optional[str] = None,
):
    """multi-theme-trade 중심 반복 실행 스케줄러."""
    from src.runner import MultiThemeLeaderTradingRunner
    from src.runner.multi_theme_scheduler import MultiThemeScheduler

    if execute and not (paper or dry_run):
        raise ValueError("--multi-theme-trade --execute requires --paper or --dry-run")

    dry_run_override = True
    trading_enabled_override = True
    account_type_override = "paper"
    if paper and execute:
        dry_run_override = False

    trade_runner = MultiThemeLeaderTradingRunner(
        config_path=config_path,
        dry_run_override=dry_run_override,
        trading_enabled_override=trading_enabled_override,
        account_type_override=account_type_override,
    )
    scheduler = MultiThemeScheduler(
        trade_runner=trade_runner,
        short_interval_minutes=trade_interval_minutes,
        short_market_hours_only=market_hours_only,
        long_plan_time=long_plan_time,
        long_plan_window_minutes=long_plan_window_minutes,
        long_trigger_check_minutes=long_trigger_check_minutes,
        long_market_hours_only=long_market_hours_only,
        collect_interval_minutes=collect_interval_minutes,
        collect_command=collect_command,
    )
    scheduler.run_loop(
        candidate_limit=candidate_limit,
        per_theme_top_n=per_theme_top_n,
        short_top_n=top_n,
        long_top_n=top_n,
        execute=execute,
        min_leader_score=min_leader_score,
        min_confidence=min_confidence,
        max_risk_level=max_risk_level,
        short_strategy_profile="short",
        long_strategy_profile="long",
    )


# ==========================================
# 메인 엔트리포인트
# ==========================================

def main():
    parser = argparse.ArgumentParser(
        description="HQA - AI 기반 멀티 에이전트 주식 분석 시스템",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        "-s", "--stock",
        type=str,
        help="종목 전체 분석 (종목명 또는 종목코드)"
    )
    
    parser.add_argument(
        "-q", "--quick",
        type=str,
        help="빠른 분석 (Quant + Chartist)"
    )
    
    parser.add_argument(
        "-p", "--price",
        type=str,
        help="실시간 시세 조회"
    )

    parser.add_argument(
        "--theme",
        type=str,
        help="테마 데이터 기반 주도주 자동 선정"
    )

    parser.add_argument(
        "--theme-trade",
        type=str,
        help="테마 주도주를 발굴한 뒤 거래 미리보기 또는 실행"
    )

    parser.add_argument(
        "--theme-trade-report",
        type=str,
        help="저장된 theme-trade preview 리포트를 재평가 없이 거래 미리보기 또는 실행"
    )

    parser.add_argument(
        "--multi-theme-trade",
        action="store_true",
        help="전체 테마 순회 후 통합 랭킹으로 상위 주도주 미리보기 또는 실행"
    )

    parser.add_argument(
        "--theme-key",
        type=str,
        default="",
        help="[--theme/--theme-trade와 함께] 저장된 테마 키"
    )

    parser.add_argument(
        "--candidate-limit",
        type=int,
        default=5,
        help="[--theme와 함께] 평가할 후보 종목 수 (기본: 5)"
    )

    parser.add_argument(
        "--top-n",
        type=int,
        default=3,
        help="[--theme와 함께] 최종 반환할 주도주 수 (기본: 3)"
    )

    parser.add_argument(
        "--execute-top-n",
        type=int,
        default=1,
        help="[--theme-trade와 함께] 거래 preview/execute 대상 상위 종목 수"
    )

    parser.add_argument(
        "--min-leader-score",
        type=int,
        default=None,
        help="[--theme-trade와 함께] 최소 leader_score"
    )

    parser.add_argument(
        "--min-confidence",
        type=int,
        default=None,
        help="[--multi-theme-trade와 함께] 최소 confidence (기본: 65)"
    )

    parser.add_argument(
        "--max-risk-level",
        type=str,
        default=None,
        help="[--multi-theme-trade와 함께] 허용 최대 risk_level_code (기본: MEDIUM)"
    )

    parser.add_argument(
        "--strategy-profile",
        type=str,
        default="default",
        choices=["default", "short", "long"],
        help="[--theme-trade/--multi-theme-trade와 함께] 전략 프로필 가중치 (기본: default)"
    )

    parser.add_argument(
        "--preview",
        action="store_true",
        help="[--theme-trade와 함께] 주문 미리보기만 실행"
    )

    parser.add_argument(
        "--execute",
        action="store_true",
        help="[--theme-trade와 함께] 조건 충족 시 주문 실행"
    )

    parser.add_argument(
        "--paper",
        action="store_true",
        help="[--theme-trade --execute와 함께] KIS 모의투자 주문 경로 사용"
    )

    parser.add_argument(
        "--trade-interval-minutes",
        type=int,
        default=60,
        help="[--multi-theme-trade --loop] 거래 실행 주기(분)"
    )

    parser.add_argument(
        "--market-hours-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="[--multi-theme-trade --loop] 장중(09:00~15:30 KST)만 거래 실행 (기본: true, 비활성화: --no-market-hours-only)"
    )

    parser.add_argument(
        "--collect-interval-minutes",
        type=int,
        default=None,
        help="[--multi-theme-trade --loop] 데이터 수집 주기(분)"
    )

    parser.add_argument(
        "--collect-command",
        type=str,
        default=None,
        help="[--multi-theme-trade --loop] 주기 수집에 사용할 커맨드"
    )

    parser.add_argument(
        "--long-plan-time",
        type=str,
        default="08:00",
        help='[--multi-theme-trade --loop] 장기 전략 플랜 생성 시각 "HH:MM" (기본: 08:00)'
    )

    parser.add_argument(
        "--long-trigger-check-minutes",
        type=int,
        default=5,
        help="[--multi-theme-trade --loop] 장기 전략 트리거 점검 주기(분)"
    )

    parser.add_argument(
        "--long-plan-window-minutes",
        type=int,
        default=40,
        help='[--multi-theme-trade --loop] 장기 전략 플랜 생성 허용 창(분). 예: 08:00~08:40'
    )
    
    parser.add_argument(
        "--auto",
        action="store_true",
        help="자율 에이전트 모드 (config/watchlist.yaml 기반)"
    )
    
    parser.add_argument(
        "--loop",
        action="store_true",
        help="[--auto와 함께] 스케줄 반복 실행"
    )
    
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="[--auto와 함께] 매매 시뮬레이션 (실제 주문 안 함)"
    )
    
    parser.add_argument(
        "--config",
        type=str,
        default="config/watchlist.yaml",
        help="자율 에이전트 설정 파일 경로 (기본: config/watchlist.yaml)"
    )
    
    parser.add_argument(
        "--help-full",
        action="store_true",
        help="상세 도움말 표시"
    )
    
    args = parser.parse_args()
    
    # 상세 도움말
    if args.help_full:
        show_help()
        return
    
    # 자율 에이전트 모드
    if args.auto:
        run_autonomous_mode(
            config_path=args.config,
            loop=args.loop,
            dry_run=args.dry_run,
        )
        return

    if args.theme_trade:
        if args.preview and args.execute:
            parser.error("--theme-trade에서는 --preview와 --execute를 동시에 사용할 수 없습니다.")
        if args.execute and not (args.paper or args.dry_run):
            parser.error("--theme-trade --execute는 --paper 또는 --dry-run을 함께 지정해야 합니다.")
        run_theme_trading_mode(
            theme=args.theme_trade,
            theme_key=args.theme_key,
            candidate_limit=args.candidate_limit,
            top_n=args.top_n,
            execute_top_n=args.execute_top_n,
            execute=args.execute,
            min_leader_score=args.min_leader_score,
            config_path=args.config,
            paper=args.paper,
            dry_run=args.dry_run,
            strategy_profile=args.strategy_profile,
        )
        return

    if args.theme_trade_report:
        if args.preview and args.execute:
            parser.error("--theme-trade-report에서는 --preview와 --execute를 동시에 사용할 수 없습니다.")
        if args.execute and not (args.paper or args.dry_run):
            parser.error("--theme-trade-report --execute는 --paper 또는 --dry-run을 함께 지정해야 합니다.")
        run_theme_report_trading_mode(
            report_path=args.theme_trade_report,
            execute_top_n=args.execute_top_n,
            execute=args.execute,
            config_path=args.config,
            paper=args.paper,
            dry_run=args.dry_run,
        )
        return

    if args.multi_theme_trade:
        if args.preview and args.execute:
            parser.error("--multi-theme-trade에서는 --preview와 --execute를 동시에 사용할 수 없습니다.")
        if args.execute and not (args.paper or args.dry_run):
            parser.error("--multi-theme-trade --execute는 --paper 또는 --dry-run을 함께 지정해야 합니다.")
        if args.loop:
            run_multi_theme_trading_loop_mode(
                top_n=args.execute_top_n,
                per_theme_top_n=args.top_n,
                candidate_limit=args.candidate_limit,
                execute=args.execute,
                min_leader_score=args.min_leader_score,
                min_confidence=args.min_confidence,
                max_risk_level=args.max_risk_level,
                strategy_profile=args.strategy_profile,
                config_path=args.config,
                paper=args.paper,
                dry_run=args.dry_run,
                trade_interval_minutes=args.trade_interval_minutes,
                market_hours_only=args.market_hours_only,
                long_plan_time=args.long_plan_time,
                long_plan_window_minutes=args.long_plan_window_minutes,
                long_trigger_check_minutes=args.long_trigger_check_minutes,
                long_market_hours_only=args.market_hours_only,
                collect_interval_minutes=args.collect_interval_minutes,
                collect_command=args.collect_command,
            )
            return
        run_multi_theme_trading_mode(
            top_n=args.execute_top_n,
            per_theme_top_n=args.top_n,
            candidate_limit=args.candidate_limit,
            execute=args.execute,
            min_leader_score=args.min_leader_score,
            min_confidence=args.min_confidence,
            max_risk_level=args.max_risk_level,
            strategy_profile=args.strategy_profile,
            config_path=args.config,
            paper=args.paper,
            dry_run=args.dry_run,
        )
        return
    
    # 실시간 시세
    if args.price:
        show_realtime_price(args.price)
        return

    # 테마 주도주 선정
    if args.theme:
        run_theme_orchestration(
            args.theme,
            theme_key=args.theme_key,
            candidate_limit=args.candidate_limit,
            top_n=args.top_n,
        )
        return

    # 빠른 분석
    if args.quick:
        run_stock_analysis(args.quick, quick=True)
        return
    
    # 전체 분석
    if args.stock:
        run_stock_analysis(args.stock, quick=False)
        return
    
    # 기본: 대화형 모드
    run_interactive_mode()


if __name__ == "__main__":
    main()
