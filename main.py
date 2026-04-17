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
import json
import sys
from datetime import datetime
from pathlib import Path
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


def get_data_dir():
    from src.config.settings import get_data_dir as _get_data_dir
    return _get_data_dir()


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


def run_theme_orchestration(theme: str, candidate_limit: int = 5, top_n: int = 3):
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
        evidence_rows = leader.get("evidence", []) or []
        if evidence_rows:
            print("근거:")
            for evidence_idx, row in enumerate(evidence_rows[:5], start=1):
                source = row.get("source", "unknown")
                title = row.get("title", "") or "(untitled)"
                snippet = row.get("snippet", "") or ""
                note = row.get("note", "") or ""
                print(f"  [{evidence_idx}] {source} | {title}")
                if note:
                    print(f"      note: {note}")
                if snippet:
                    print(f"      snippet: {snippet[:180]}")

    report_path = _save_theme_analysis_report(result)
    if report_path:
        print(f"\n📄 Evidence report saved: {report_path}")

    return result


def _save_theme_analysis_report(result: dict) -> Optional[str]:
    if not isinstance(result, dict) or result.get("status") != "success":
        return None

    data_dir = get_data_dir()
    report_dir = Path(data_dir) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    theme_key = result.get("theme_key") or result.get("theme") or "theme"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"{theme_key}_theme_analysis_{timestamp}.json"
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return str(report_path)


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
            for evidence_idx, row in enumerate((leader.get("evidence", []) or [])[:3], start=1):
                source = row.get("source", "unknown")
                title = row.get("title", "") or "(untitled)"
                lines.append(f"   근거 {evidence_idx}: [{source}] {title}")
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
    
    # 실시간 시세
    if args.price:
        show_realtime_price(args.price)
        return

    # 테마 주도주 선정
    if args.theme:
        run_theme_orchestration(
            args.theme,
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
