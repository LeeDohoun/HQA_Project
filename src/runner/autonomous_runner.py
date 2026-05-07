# 파일: src/runner/autonomous_runner.py
"""
자율 에이전트 실행기 (Autonomous Runner)

config/watchlist.yaml 설정을 읽고:
1. 감시 종목을 자동으로 분석
2. 매매 조건 충족 시 TradeExecutor로 주문 실행
3. 트레이싱 기록 저장

실행 모드:
- run_once(): 감시 목록 1회 순회
- run_loop(): 스케줄 기반 반복 실행

사용법:
    python main.py --auto              # 1회 실행
    python main.py --auto --loop       # 반복 실행
    python main.py --auto --dry-run    # 매매 시뮬레이션
"""

import logging
import time
import yaml
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.runner.trade_executor import TradeExecutor

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


class AutonomousRunner:
    """
    자율 에이전트 실행기

    설정 파일 기반으로 종목을 분석하고 매매를 실행합니다.
    """

    def __init__(
        self,
        config_path: str = "config/watchlist.yaml",
        dry_run_override: Optional[bool] = None,
    ):
        """
        Args:
            config_path: YAML 설정 파일 경로
            dry_run_override: True면 설정과 무관하게 dry_run 강제
        """
        self._config_path = Path(config_path)
        self._config = self._load_config()

        # dry_run 오버라이드
        if dry_run_override is not None:
            self._config.setdefault("trading", {})["dry_run"] = dry_run_override

        # 트레이싱 설정
        tracing_config = self._config.get("tracing", {})
        self._debug_trace = tracing_config.get("debug", False)

        # 매매 실행기
        trading_config = self._config.get("trading", {})
        self._executor = TradeExecutor(trading_config)

        # 실행 이력
        self._run_history: List[Dict[str, Any]] = []

    def _load_config(self) -> Dict[str, Any]:
        """YAML 설정 로드"""
        if not self._config_path.exists():
            logger.warning(f"[Runner] 설정 파일 없음: {self._config_path}")
            return {
                "schedule": {"enabled": False, "interval_minutes": 60},
                "watchlist": [],
                "trading": {"enabled": False, "dry_run": True},
            }

        with open(self._config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}

        logger.info(f"[Runner] 설정 로드: {self._config_path}")
        return config

    def reload_config(self) -> None:
        """설정 파일 재로드 (운영 중 변경 감지)"""
        self._config = self._load_config()
        trading_config = self._config.get("trading", {})
        self._executor = TradeExecutor(trading_config)
        logger.info("[Runner] 설정 재로드 완료")

    @property
    def watchlist(self) -> List[Dict[str, str]]:
        """감시 종목 리스트"""
        items = self._config.get("watchlist", [])
        # priority 순 정렬
        return sorted(items, key=lambda x: x.get("priority", 99))

    # ──────────────────────────────────────────────
    # 1회 실행
    # ──────────────────────────────────────────────

    def run_once(self) -> List[Dict[str, Any]]:
        """
        감시 목록 1회 순회 분석

        Returns:
            각 종목의 분석 결과 리스트
        """
        watchlist = self.watchlist
        if not watchlist:
            print("⚠️  감시 종목이 설정되지 않았습니다.")
            print(f"   → {self._config_path} 에 종목을 추가하세요.")
            return []

        now = datetime.now(KST)
        print(f"\n{'='*60}")
        print(f"🤖 [자율 에이전트] 분석 시작 — {now.strftime('%Y-%m-%d %H:%M:%S KST')}")
        print(f"   감시 종목: {len(watchlist)}개")
        print(f"   매매 모드: {'🧪 시뮬레이션 (dry_run)' if self._executor.is_dry_run else '🔴 실전 매매'}")
        if not self._executor.is_enabled:
            print(f"   매매 상태: ⏸️  비활성 (분석만 실행)")
        print(f"{'='*60}\n")

        results = []

        for idx, stock in enumerate(watchlist, 1):
            name = stock.get("name", "")
            code = stock.get("code", "")
            mode = stock.get("mode", "full")

            if not name or not code:
                logger.warning(f"[Runner] 종목 정보 불완전: {stock}")
                continue

            print(f"\n{'─'*50}")
            print(f"📊 [{idx}/{len(watchlist)}] {name}({code}) — {mode} 분석")
            print(f"{'─'*50}")

            try:
                result = self._analyze_stock(name, code, mode)
                result["stock_name"] = name
                result["stock_code"] = code
                result["mode"] = mode

                # 매매 판단
                decision = result.get("final_decision")
                if decision and self._executor.is_enabled:
                    trade_result = self._evaluate_trade(name, code, decision)
                    result["trade"] = trade_result

                results.append(result)

            except Exception as e:
                logger.exception(f"[Runner] {name}({code}) 분석 오류: {e}")
                print(f"   ❌ 분석 오류: {e}")
                results.append({
                    "stock_name": name,
                    "stock_code": code,
                    "status": "error",
                    "error": str(e)[:200],
                })

        # 요약 출력
        self._print_summary(results)

        # 이력 저장
        self._run_history.append({
            "timestamp": now.isoformat(),
            "results_count": len(results),
            "success_count": sum(1 for r in results if r.get("status") != "error"),
        })

        return results

    def _analyze_stock(
        self, stock_name: str, stock_code: str, mode: str
    ) -> Dict[str, Any]:
        """단일 종목 분석"""
        if mode == "quick":
            return self._quick_analysis(stock_name, stock_code)
        else:
            return self._full_analysis(stock_name, stock_code)

    def _full_analysis(
        self, stock_name: str, stock_code: str
    ) -> Dict[str, Any]:
        """전체 분석 (LangGraph)"""
        from src.agents.graph import run_stock_analysis

        result = run_stock_analysis(
            stock_name=stock_name,
            stock_code=stock_code,
            max_retries=1,
            debug_trace=self._debug_trace,
        )

        # 결과 요약 출력
        scores = result.get("scores", {})
        analyst = scores.get("analyst")
        quant = scores.get("quant")
        chartist = scores.get("chartist")
        decision = result.get("final_decision")

        if analyst:
            print(f"   → Analyst:  {analyst.hegemony_grade}등급 ({analyst.total_score}/70)")
        if quant:
            print(f"   → Quant:    {quant.grade} ({quant.total_score}/100)")
        if chartist:
            print(f"   → Chartist: {chartist.signal} ({chartist.total_score}/100)")
        if decision:
            print(
                f"   → 최종판단: {decision.action.value} "
                f"({decision.total_score}/270, 확신:{decision.confidence}%)"
            )

        return result

    def _quick_analysis(
        self, stock_name: str, stock_code: str
    ) -> Dict[str, Any]:
        """빠른 분석 (Quant + Chartist)"""
        from src.agents import QuantAgent, ChartistAgent
        from src.utils.parallel import run_agents_parallel, is_error

        quant = QuantAgent()
        chartist = ChartistAgent()

        parallel_results = run_agents_parallel({
            "quant": (quant.full_analysis, (stock_name, stock_code)),
            "chartist": (chartist.full_analysis, (stock_name, stock_code)),
        })

        quant_score = parallel_results.get("quant")
        chartist_score = parallel_results.get("chartist")

        if is_error(quant_score):
            quant_score = quant._default_score(stock_name, str(quant_score))
        if is_error(chartist_score):
            chartist_score = chartist._default_score(stock_code, str(chartist_score))

        print(f"   → Quant:    {quant_score.grade} ({quant_score.total_score}/100)")
        print(f"   → Chartist: {chartist_score.signal} ({chartist_score.total_score}/100)")

        return {
            "status": "success",
            "mode": "quick",
            "scores": {"quant": quant_score, "chartist": chartist_score},
        }

    # ──────────────────────────────────────────────
    # 매매 판단
    # ──────────────────────────────────────────────

    def _evaluate_trade(
        self, stock_name: str, stock_code: str, decision
    ) -> Dict[str, Any]:
        """
        FinalDecision을 기반으로 매매 실행 여부 결정

        Returns:
            매매 결과 dict
        """
        # 현재가 조회 시도
        current_price = self._get_current_price(stock_code)

        if self._executor.should_buy(decision):
            print(f"   💰 매수 조건 충족! (점수:{decision.total_score}, 확신:{decision.confidence}%)")
            return self._executor.execute_buy(
                stock_name, stock_code, decision, current_price
            )

        elif self._executor.should_sell(decision):
            print(f"   📉 매도 조건 충족! (점수:{decision.total_score})")
            return self._executor.execute_sell(
                stock_name, stock_code, decision, quantity=0, current_price=current_price
            )

        else:
            reason = f"조건 미충족 (점수:{decision.total_score}, 행동:{decision.action.value})"
            print(f"   ⏸️  매매 대기 — {reason}")
            return {"status": "no_action", "reason": reason}

    def _get_current_price(self, stock_code: str) -> Optional[int]:
        """KIS API로 현재가 조회"""
        try:
            from src.tools.realtime_tool import KISRealtimeTool

            tool = KISRealtimeTool()
            if tool.is_available:
                quote = tool.get_current_price(stock_code)
                if quote and isinstance(quote, dict):
                    return int(quote.get("stck_prpr", 0))
        except Exception as e:
            logger.debug(f"[Runner] 현재가 조회 실패: {e}")

        return None

    # ──────────────────────────────────────────────
    # 반복 실행 (스케줄)
    # ──────────────────────────────────────────────

    def run_loop(self) -> None:
        """
        스케줄 기반 반복 실행

        config/watchlist.yaml의 schedule 설정에 따라:
        - interval_minutes 간격으로 반복
        - market_hours_only=True면 장중에만 실행
        """
        schedule = self._config.get("schedule", {})
        interval = schedule.get("interval_minutes", 60)
        market_only = schedule.get("market_hours_only", True)

        print(f"\n🔁 [자율 에이전트] 반복 실행 시작")
        print(f"   주기: {interval}분")
        print(f"   장중만: {'예' if market_only else '아니오'}")
        print(f"   중지: Ctrl+C\n")

        try:
            while True:
                now = datetime.now(KST)

                if market_only and not self._is_market_hours(now):
                    next_open = self._next_market_open(now)
                    wait_seconds = (next_open - now).total_seconds()
                    print(
                        f"⏰ 장외 시간 — 다음 장 시작: "
                        f"{next_open.strftime('%Y-%m-%d %H:%M KST')} "
                        f"({wait_seconds/3600:.1f}시간 후)"
                    )
                    time.sleep(min(wait_seconds, 300))  # 최대 5분 단위 체크
                    continue

                # 분석 실행
                self.run_once()

                # 설정 재로드 (운영 중 변경 반영)
                self.reload_config()

                # 대기
                print(f"\n⏳ 다음 실행까지 {interval}분 대기...")
                time.sleep(interval * 60)

        except KeyboardInterrupt:
            print("\n\n👋 자율 에이전트 종료")
            self._print_daily_summary()

    def _is_market_hours(self, now: Optional[datetime] = None) -> bool:
        """장중 여부 확인 (09:00~15:30 KST, 평일)"""
        if now is None:
            now = datetime.now(KST)

        # 주말 제외
        if now.weekday() >= 5:
            return False

        # 장중 시간
        market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)

        return market_open <= now <= market_close

    def _next_market_open(self, now: datetime) -> datetime:
        """다음 장 시작 시각 계산"""
        next_open = now.replace(hour=9, minute=0, second=0, microsecond=0)

        if now.hour >= 15 or (now.hour == 15 and now.minute >= 30):
            next_open += timedelta(days=1)

        # 주말 건너뛰기
        while next_open.weekday() >= 5:
            next_open += timedelta(days=1)

        return next_open

    # ──────────────────────────────────────────────
    # 요약 출력
    # ──────────────────────────────────────────────

    def _print_summary(self, results: List[Dict[str, Any]]) -> None:
        """분석 결과 요약 출력"""
        print(f"\n{'='*60}")
        print(f"📋 [분석 요약]")
        print(f"{'='*60}")

        for r in results:
            name = r.get("stock_name", "?")
            code = r.get("stock_code", "?")
            status = r.get("status", "?")

            if status == "error":
                print(f"   ❌ {name}({code}): 오류 — {r.get('error', '')[:50]}")
                continue

            decision = r.get("final_decision")
            if decision:
                print(
                    f"   {'📈' if decision.action.value in ['적극 매수', '매수'] else '⏸️'} "
                    f"{name}({code}): {decision.action.value} "
                    f"({decision.total_score}점, 확신:{decision.confidence}%)"
                )

                trade = r.get("trade", {})
                trade_status = trade.get("status", "")
                if trade_status == "simulated":
                    print(f"      🧪 매매 시뮬레이션 완료")
                elif trade_status == "no_action":
                    print(f"      ⏸️  매매 대기")
            else:
                scores = r.get("scores", {})
                quant = scores.get("quant")
                if quant:
                    print(f"   📊 {name}({code}): Quant {quant.total_score}/100")

        # 매매 일일 요약
        if self._executor.is_enabled:
            summary = self._executor.get_daily_summary()
            print(f"\n   💰 일일 매수 누적: {summary['total_spent']:,.0f}원")
            print(f"   💰 잔여 예산: {summary['remaining_budget']:,.0f}원")

        print(f"{'='*60}\n")

    def _print_daily_summary(self) -> None:
        """일일 전체 실행 요약"""
        if not self._run_history:
            return

        print(f"\n{'='*60}")
        print(f"📊 [일일 실행 요약]")
        print(f"{'='*60}")
        print(f"   총 실행 횟수: {len(self._run_history)}")

        if self._executor.is_enabled:
            summary = self._executor.get_daily_summary()
            print(f"   총 매수 금액: {summary['total_spent']:,.0f}원")
            print(f"   총 주문 건수: {summary['order_count']}")

        print(f"{'='*60}\n")
