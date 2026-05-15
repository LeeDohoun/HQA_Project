from __future__ import annotations

import logging
import shlex
import subprocess
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.runner.multi_theme_leader_trading_runner import MultiThemeLeaderTradingRunner
from src.runner.decision_adapter import build_final_decision_from_payload
from src.runner.trade_executor import SellTriggerContext

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


class MultiThemeScheduler:
    """Short/Long strategy scheduler around multi-theme runner with background collection."""

    def __init__(
        self,
        *,
        trade_runner: MultiThemeLeaderTradingRunner,
        short_interval_minutes: int = 60,
        short_market_hours_only: bool = True,
        long_plan_time: str = "08:00",
        long_plan_window_minutes: int = 40,
        long_trigger_check_minutes: int = 5,
        long_market_hours_only: bool = True,
        long_buy_trigger_pct: float = -1.0,
        long_sell_trigger_pct: float = 1.0,
        long_plan_ttl_hours: int = 24,
        collect_interval_minutes: Optional[int] = None,
        collect_command: Optional[str] = None,
        poll_seconds: int = 30,
    ):
        self._trade_runner = trade_runner
        self._short_interval_minutes = max(1, int(short_interval_minutes))
        self._short_market_hours_only = bool(short_market_hours_only)
        self._long_plan_time = str(long_plan_time or "08:00")
        self._long_plan_window_minutes = max(1, int(long_plan_window_minutes))
        self._long_trigger_check_minutes = max(1, int(long_trigger_check_minutes))
        self._long_market_hours_only = bool(long_market_hours_only)
        self._long_buy_trigger_pct = float(long_buy_trigger_pct)
        self._long_sell_trigger_pct = float(long_sell_trigger_pct)
        self._long_plan_ttl_hours = max(1, int(long_plan_ttl_hours))
        self._collect_interval_minutes = (
            max(1, int(collect_interval_minutes))
            if collect_interval_minutes is not None
            else None
        )
        self._collect_command = str(collect_command or "").strip()
        self._poll_seconds = max(5, int(poll_seconds))

        self._last_short_trade_at: Optional[datetime] = None
        self._last_short_premarket_date: Optional[str] = None
        self._last_long_plan_date: Optional[str] = None
        self._last_long_trigger_check_at: Optional[datetime] = None
        self._last_collect_at: Optional[datetime] = None
        self._long_candidates: List[Dict[str, Any]] = []
        self._last_holding_check_at: Optional[datetime] = None
        self._holding_state: Dict[str, Dict[str, Any]] = {}

    def run_loop(
        self,
        *,
        candidate_limit: int = 5,
        per_theme_top_n: int = 3,
        short_top_n: int = 3,
        long_top_n: int = 3,
        execute: bool = False,
        min_leader_score: Optional[int] = None,
        min_confidence: Optional[int] = None,
        max_risk_level: Optional[str] = None,
        short_strategy_profile: str = "short",
        long_strategy_profile: str = "long",
        include_theme_keys: Optional[Sequence[str]] = None,
        exclude_theme_keys: Optional[Sequence[str]] = None,
    ) -> None:
        print("\n🔁 [Multi-Theme Scheduler] 시작")
        print(
            f"   단기: {self._short_interval_minutes}분 "
            f"(장중만={'예' if self._short_market_hours_only else '아니오'})"
        )
        print(
            f"   장기: {self._long_plan_time} 플랜 생성 / "
            f"{self._long_trigger_check_minutes}분 트리거 점검 "
            f"(장중만={'예' if self._long_market_hours_only else '아니오'})"
        )
        if self._collect_interval_minutes and self._collect_command:
            print(
                f"   데이터 수집: {self._collect_interval_minutes}분 주기 "
                f"({self._collect_command})"
            )
        else:
            print("   데이터 수집: 비활성")
        print("   중지: Ctrl+C\n")

        try:
            while True:
                now = datetime.now(KST)

                if self._collect_interval_minutes and self._collect_command:
                    if self._is_due(now, self._last_collect_at, self._collect_interval_minutes):
                        self._run_collection()
                        self._last_collect_at = now

                if self._should_run_short_cycle(now):
                    # 1) short 아이디어 탐색/랭킹
                    preview = self._trade_runner.run_all(
                        candidate_limit=candidate_limit,
                        per_theme_top_n=per_theme_top_n,
                        top_n=short_top_n,
                        execute=False,
                        min_leader_score=min_leader_score,
                        min_confidence=min_confidence,
                        max_risk_level=max_risk_level,
                        buy_only=self._resolve_buy_only(stage="short_entry", now=now),
                        strategy_profile=short_strategy_profile,
                        include_theme_keys=include_theme_keys,
                        exclude_theme_keys=exclude_theme_keys,
                        save_report=True,
                    )
                    self._last_short_trade_at = now
                    print(
                        "🏁 [Short/Preview] "
                        f"selected={preview.get('selected_count', 0)} "
                        f"summary={preview.get('summary', {})} "
                        f"report={preview.get('report_path')}"
                    )

                    # 2) 주문 직전 재평가 후 집행
                    if execute and int(preview.get("selected_count", 0) or 0) > 0:
                        if self._short_market_hours_only and not self._is_market_hours(datetime.now(KST)):
                            print("⏸️ [Short/Execute] 장중 종료로 집행 스킵")
                        elif self._is_no_new_entry_window(datetime.now(KST)):
                            print("⏸️ [Short/Execute] 15:10~15:30 신규진입 중지 구간으로 집행 스킵")
                        else:
                            exec_min_leader, exec_min_conf = self._resolve_short_execution_gates(
                                base_min_leader_score=min_leader_score,
                                base_min_confidence=min_confidence,
                                now=datetime.now(KST),
                            )
                            executed = self._trade_runner.run_all(
                                candidate_limit=candidate_limit,
                                per_theme_top_n=per_theme_top_n,
                                top_n=short_top_n,
                                execute=True,
                                min_leader_score=exec_min_leader,
                                min_confidence=exec_min_conf,
                                max_risk_level=max_risk_level,
                                buy_only=self._resolve_buy_only(stage="short_entry", now=now),
                                strategy_profile=short_strategy_profile,
                                include_theme_keys=include_theme_keys,
                                exclude_theme_keys=exclude_theme_keys,
                                save_report=True,
                            )
                            print(
                                "🏁 [Short/Execute] "
                                f"selected={executed.get('selected_count', 0)} "
                                f"summary={executed.get('summary', {})} "
                                f"report={executed.get('report_path')}"
                            )

                if self._should_build_long_plan(now):
                    self._build_long_plan(
                        now=now,
                        candidate_limit=candidate_limit,
                        per_theme_top_n=per_theme_top_n,
                        long_top_n=long_top_n,
                        min_leader_score=min_leader_score,
                        min_confidence=min_confidence,
                        max_risk_level=max_risk_level,
                        long_strategy_profile=long_strategy_profile,
                        include_theme_keys=include_theme_keys,
                        exclude_theme_keys=exclude_theme_keys,
                    )

                if self._should_check_long_triggers(now):
                    self._check_long_triggers(
                        now=now,
                        candidate_limit=candidate_limit,
                        per_theme_top_n=per_theme_top_n,
                        min_leader_score=min_leader_score,
                        min_confidence=min_confidence,
                        max_risk_level=max_risk_level,
                        long_strategy_profile=long_strategy_profile,
                        include_theme_keys=include_theme_keys,
                        exclude_theme_keys=exclude_theme_keys,
                        execute=execute,
                    )

                if self._should_check_holding_triggers(now):
                    self._check_holding_positions(
                        now=now,
                        candidate_limit=candidate_limit,
                        per_theme_top_n=per_theme_top_n,
                        min_leader_score=min_leader_score,
                        min_confidence=min_confidence,
                        max_risk_level=max_risk_level,
                        short_strategy_profile=short_strategy_profile,
                        include_theme_keys=include_theme_keys,
                        exclude_theme_keys=exclude_theme_keys,
                        execute=execute,
                    )

                time.sleep(self._poll_seconds)
        except KeyboardInterrupt:
            print("\n👋 [Multi-Theme Scheduler] 종료")

    @staticmethod
    def _is_due(now: datetime, last_at: Optional[datetime], interval_minutes: int) -> bool:
        if last_at is None:
            return True
        elapsed = (now - last_at).total_seconds() / 60.0
        return elapsed >= max(1, interval_minutes)

    @staticmethod
    def _resolve_buy_only(*, stage: str, now: datetime) -> bool:
        _ = now
        if stage == "holding_review":
            return False
        return True

    @staticmethod
    def _is_market_hours(now: Optional[datetime] = None) -> bool:
        current = now or datetime.now(KST)
        if current.weekday() >= 5:
            return False
        market_open = current.replace(hour=9, minute=0, second=0, microsecond=0)
        market_close = current.replace(hour=15, minute=30, second=0, microsecond=0)
        # Strict window: [09:00:00, 15:30:00)
        return market_open <= current < market_close

    def _should_run_short_cycle(self, now: datetime) -> bool:
        # 장전 분석 1회: 08:00~08:40
        if self._is_short_premarket_window(now):
            date_key = now.strftime("%Y-%m-%d")
            if self._last_short_premarket_date != date_key:
                self._last_short_premarket_date = date_key
                return True
        if self._short_market_hours_only and not self._is_market_hours(now):
            return False
        return self._is_due(now, self._last_short_trade_at, self._short_interval_minutes)

    @staticmethod
    def _is_short_premarket_window(now: datetime) -> bool:
        if now.weekday() >= 5:
            return False
        start = now.replace(hour=8, minute=0, second=0, microsecond=0)
        end = now.replace(hour=8, minute=40, second=0, microsecond=0)
        return start <= now < end

    @staticmethod
    def _is_conservative_entry_window(now: datetime) -> bool:
        if now.weekday() >= 5:
            return False
        start = now.replace(hour=9, minute=0, second=0, microsecond=0)
        end = now.replace(hour=9, minute=10, second=0, microsecond=0)
        return start <= now < end

    @staticmethod
    def _is_no_new_entry_window(now: datetime) -> bool:
        if now.weekday() >= 5:
            return False
        start = now.replace(hour=15, minute=10, second=0, microsecond=0)
        end = now.replace(hour=15, minute=30, second=0, microsecond=0)
        return start <= now < end

    def _resolve_short_execution_gates(
        self,
        *,
        base_min_leader_score: Optional[int],
        base_min_confidence: Optional[int],
        now: datetime,
    ) -> Tuple[Optional[int], Optional[int]]:
        if not self._is_conservative_entry_window(now):
            return base_min_leader_score, base_min_confidence

        # 09:00~09:10 신규진입 보수화: 임계값 상향
        min_leader = 80 if base_min_leader_score is None else base_min_leader_score + 5
        min_conf = 70 if base_min_confidence is None else base_min_confidence + 5
        print(f"🛡️ [Short/Execute] 보수화 구간 적용: min_leader={min_leader}, min_conf={min_conf}")
        return min_leader, min_conf

    def _should_check_holding_triggers(self, now: datetime) -> bool:
        if self._short_market_hours_only and not self._is_market_hours(now):
            return False
        interval = min(self._short_interval_minutes, self._long_trigger_check_minutes)
        return self._is_due(now, self._last_holding_check_at, max(1, interval))

    def _should_build_long_plan(self, now: datetime) -> bool:
        if now.weekday() >= 5:
            return False
        hh, mm = self._parse_hhmm(self._long_plan_time)
        plan_start = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        plan_end = plan_start + timedelta(minutes=self._long_plan_window_minutes)
        # Strict daily window: run only in [plan_time, plan_time+window)
        if not (plan_start <= now < plan_end):
            return False
        today_key = now.strftime("%Y-%m-%d")
        return self._last_long_plan_date != today_key

    def _should_check_long_triggers(self, now: datetime) -> bool:
        if self._long_market_hours_only and not self._is_market_hours(now):
            return False
        if not self._long_candidates:
            return False
        return self._is_due(now, self._last_long_trigger_check_at, self._long_trigger_check_minutes)

    def _build_long_plan(
        self,
        *,
        now: datetime,
        candidate_limit: int,
        per_theme_top_n: int,
        long_top_n: int,
        min_leader_score: Optional[int],
        min_confidence: Optional[int],
        max_risk_level: Optional[str],
        long_strategy_profile: str,
        include_theme_keys: Optional[Sequence[str]],
        exclude_theme_keys: Optional[Sequence[str]],
    ) -> None:
        result = self._trade_runner.run_all(
            candidate_limit=candidate_limit,
            per_theme_top_n=per_theme_top_n,
            top_n=long_top_n,
            execute=False,
            min_leader_score=min_leader_score,
            min_confidence=min_confidence,
            max_risk_level=max_risk_level,
            buy_only=self._resolve_buy_only(stage="long_entry", now=now),
            strategy_profile=long_strategy_profile,
            include_theme_keys=include_theme_keys,
            exclude_theme_keys=exclude_theme_keys,
            save_report=True,
        )
        rows = result.get("global_ranked_leaders") or []
        plan_expiry = now + timedelta(hours=self._long_plan_ttl_hours)
        candidates: List[Dict[str, Any]] = []
        for row in rows[: max(0, long_top_n)]:
            if str(row.get("action_code") or "").upper() not in {"BUY", "STRONG_BUY"}:
                continue
            candidates.append(
                {
                    "theme_key": row.get("theme_key"),
                    "theme": row.get("theme"),
                    "stock_code": row.get("stock_code"),
                    "stock_name": row.get("stock_name"),
                    "leader_score": row.get("leader_score"),
                    "anchor_price": None,
                    "target_price": None,
                    "comparator": "lte",
                    "expires_at": plan_expiry,
                }
            )
        self._long_candidates = candidates
        self._last_long_plan_date = now.strftime("%Y-%m-%d")
        self._last_long_trigger_check_at = None
        print(
            f"🧭 [Long Plan] candidates={len(self._long_candidates)} "
            f"report={result.get('report_path')}"
        )

    def _check_long_triggers(
        self,
        *,
        now: datetime,
        candidate_limit: int,
        per_theme_top_n: int,
        min_leader_score: Optional[int],
        min_confidence: Optional[int],
        max_risk_level: Optional[str],
        long_strategy_profile: str,
        include_theme_keys: Optional[Sequence[str]],
        exclude_theme_keys: Optional[Sequence[str]],
        execute: bool,
    ) -> None:
        self._last_long_trigger_check_at = now
        pending: List[Dict[str, Any]] = []
        for item in self._long_candidates:
            if now > item["expires_at"]:
                continue

            # 주문 직전 재평가: 동일 theme_key 기반으로 preview 재산출
            reevaluated = self._trade_runner.run_all(
                candidate_limit=candidate_limit,
                per_theme_top_n=per_theme_top_n,
                top_n=1,
                execute=False,
                min_leader_score=min_leader_score,
                min_confidence=min_confidence,
                max_risk_level=max_risk_level,
                buy_only=self._resolve_buy_only(stage="long_entry", now=now),
                strategy_profile=long_strategy_profile,
                include_theme_keys=[item["theme_key"]] if item.get("theme_key") else include_theme_keys,
                exclude_theme_keys=exclude_theme_keys,
                save_report=False,
            )
            selected = reevaluated.get("best_leader_stocks") or []
            if not selected:
                continue
            lead = selected[0]
            if str(lead.get("stock_code")) != str(item.get("stock_code")):
                continue

            # anchor/target은 현재 리더 점수 기반이 아닌 가격 기반 단순 트리거
            # 이 구현에서는 실시간 가격을 별도 호출하지 않고 재평가 후 즉시 execute path를 사용한다.
            if execute:
                done = self._trade_runner.run_all(
                    candidate_limit=candidate_limit,
                    per_theme_top_n=per_theme_top_n,
                    top_n=1,
                    execute=True,
                    min_leader_score=min_leader_score,
                    min_confidence=min_confidence,
                    max_risk_level=max_risk_level,
                    buy_only=self._resolve_buy_only(stage="long_entry", now=now),
                    strategy_profile=long_strategy_profile,
                    include_theme_keys=[item["theme_key"]] if item.get("theme_key") else include_theme_keys,
                    exclude_theme_keys=exclude_theme_keys,
                    save_report=True,
                )
                print(
                    f"🎯 [Long Trigger] theme={item.get('theme_key')} "
                    f"stock={item.get('stock_name')} report={done.get('report_path')}"
                )
                continue

            pending.append(item)
        self._long_candidates = pending

    def _check_holding_positions(
        self,
        *,
        now: datetime,
        candidate_limit: int,
        per_theme_top_n: int,
        min_leader_score: Optional[int],
        min_confidence: Optional[int],
        max_risk_level: Optional[str],
        short_strategy_profile: str,
        include_theme_keys: Optional[Sequence[str]],
        exclude_theme_keys: Optional[Sequence[str]],
        execute: bool,
    ) -> None:
        self._last_holding_check_at = now
        holdings = list(self._trade_runner.get_holdings() or [])
        if not holdings:
            return

        scan = self._trade_runner.run_all(
            candidate_limit=candidate_limit,
            per_theme_top_n=per_theme_top_n,
            top_n=max(20, len(holdings) * 2),
            execute=False,
            min_leader_score=min_leader_score,
            min_confidence=min_confidence,
            max_risk_level=max_risk_level,
            buy_only=self._resolve_buy_only(stage="holding_review", now=now),
            strategy_profile=short_strategy_profile,
            include_theme_keys=include_theme_keys,
            exclude_theme_keys=exclude_theme_keys,
            save_report=False,
        )
        ranked = list(scan.get("global_ranked_leaders") or [])
        decision_by_code: Dict[str, Dict[str, Any]] = {
            str(row.get("stock_code") or ""): (row.get("leader", {}).get("final_decision") or {})
            for row in ranked
            if str(row.get("stock_code") or "")
        }

        theme_runner = getattr(self._trade_runner, "_theme_runner", None)
        executor = getattr(theme_runner, "_executor", None)
        if executor is None:
            return
        sold = 0
        checked = 0
        for holding in holdings:
            code = str(getattr(holding, "stock_code", "") or "").strip()
            name = str(getattr(holding, "stock_name", "") or code)
            qty = int(getattr(holding, "orderable_quantity", 0) or 0)
            if not code or qty <= 0:
                continue
            checked += 1
            state = self._holding_state.setdefault(
                code,
                {
                    "first_seen_at": now,
                    "peak_profit_rate": float(getattr(holding, "profit_loss_rate", 0.0) or 0.0),
                    "last_confidence": None,
                    "last_risk_level": None,
                },
            )
            pnl_rate = float(getattr(holding, "profit_loss_rate", 0.0) or 0.0)
            state["peak_profit_rate"] = max(float(state.get("peak_profit_rate", pnl_rate)), pnl_rate)
            holding_minutes = int((now - state["first_seen_at"]).total_seconds() // 60)

            payload = decision_by_code.get(code, {})
            decision = build_final_decision_from_payload(stock_name=name, stock_code=code, payload=payload)
            context = SellTriggerContext(
                peak_profit_rate=float(state.get("peak_profit_rate", pnl_rate)),
                holding_minutes=holding_minutes,
                confidence_baseline=state.get("last_confidence"),
            )
            evaluation = executor.evaluate_sell_triggers(
                decision=decision,
                holding=holding,
                current_price=int(getattr(holding, "current_price", 0) or 0),
                context=context,
            )
            state["last_confidence"] = int(getattr(decision, "confidence", 0))
            state["last_risk_level"] = str(getattr(getattr(decision, "risk_level", None), "name", ""))
            if not evaluation.get("should_sell"):
                continue

            current_price = int(getattr(holding, "current_price", 0) or 0) or self._trade_runner.get_current_price(code)
            if execute:
                order = executor.execute_sell(
                    stock_name=name,
                    stock_code=code,
                    decision=decision,
                    quantity=qty,
                    current_price=current_price,
                )
                print(
                    f"🔻 [Holding/Sell] {name}({code}) qty={qty} status={order.get('status')} "
                    f"reasons={evaluation.get('reasons')}"
                )
            else:
                preview = executor.preview_decision(
                    stock_name=name,
                    stock_code=code,
                    decision=decision,
                    quantity=qty,
                    current_price=current_price,
                )
                print(
                    f"🧪 [Holding/Sell Preview] {name}({code}) qty={qty} status={preview.get('status')} "
                    f"reasons={evaluation.get('reasons')}"
                )
            sold += 1

        if checked:
            print(f"🧾 [Holding Check] checked={checked}, triggered={sold}")

    @staticmethod
    def _parse_hhmm(raw: str) -> Tuple[int, int]:
        text = str(raw or "").strip()
        try:
            hh_str, mm_str = text.split(":", 1)
            hh = max(0, min(23, int(hh_str)))
            mm = max(0, min(59, int(mm_str)))
            return hh, mm
        except Exception:
            return 8, 0

    def _run_collection(self) -> None:
        command = self._collect_command
        if not command:
            return
        print(f"📡 [Collect] 실행: {command}")
        try:
            subprocess.run(
                shlex.split(command),
                check=True,
                capture_output=True,
                text=True,
            )
            print("📡 [Collect] 완료")
        except subprocess.CalledProcessError as exc:
            logger.warning("collection command failed: %s", exc)
            print(f"⚠️ [Collect] 실패: code={exc.returncode}")
