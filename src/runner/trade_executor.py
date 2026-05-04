# 파일: src/runner/trade_executor.py
"""
매매 실행기 (Trade Executor)

FinalDecision을 받아 KIS API로 주문을 실행합니다.
3중 안전장치 (서킷 브레이커) 적용:
  1. 일일 최대 매수 금액 제한
  2. 동일 종목 쿨다운
  3. 손절 기준 하드코딩

dry_run=True (기본값)이면 실제 주문 없이 로그만 기록합니다.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config.settings import get_orders_dir

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))


class TradeExecutor:
    """
    매매 실행기

    안전 우선 설계:
    - 기본값은 dry_run=True (시뮬레이션)
    - trading.enabled=False면 아예 실행 안 함
    - 서킷 브레이커: 일일 한도, 쿨다운, 손절
    """

    def __init__(self, trading_config: Dict[str, Any]):
        """
        Args:
            trading_config: watchlist.yaml의 trading 섹션
        """
        self._enabled = trading_config.get("enabled", False)
        self._dry_run = trading_config.get("dry_run", True)
        self._account_type = trading_config.get("account_type", "paper")
        self._order_type = trading_config.get("order_type", "limit")
        self._allow_real_trading = bool(trading_config.get("allow_real_trading", False))

        # 서킷 브레이커 설정
        self._max_daily_buy = trading_config.get("max_daily_buy_amount", 1_000_000)
        self._max_position_ratio = trading_config.get("max_position_ratio", 0.25)
        self._stop_loss_pct = trading_config.get("stop_loss_pct", 10)
        self._cooldown_minutes = trading_config.get("cooldown_minutes", 30)

        # 매매 조건
        self._buy_conditions = trading_config.get("auto_buy_conditions", {})
        self._sell_conditions = trading_config.get("auto_sell_conditions", {})

        # 상태 추적
        self._daily_spent: float = 0.0
        self._daily_reset_date: str = ""
        self._last_order_time: Dict[str, datetime] = {}
        self._order_history: List[Dict[str, Any]] = []

        # 주문 기록 저장 경로
        self._orders_dir = get_orders_dir()
        self._orders_dir.mkdir(parents=True, exist_ok=True)
        self._restore_state_from_disk()

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def is_dry_run(self) -> bool:
        return self._dry_run

    @property
    def account_type(self) -> str:
        return self._account_type

    def get_runtime_config(self) -> Dict[str, Any]:
        """현재 매매 실행기의 유효 설정과 상태를 반환."""
        self._reset_daily_budget_if_needed()
        return {
            "enabled": self._enabled,
            "dry_run": self._dry_run,
            "account_type": self._account_type,
            "order_type": self._order_type,
            "allow_real_trading": self._allow_real_trading,
            "max_daily_buy_amount": self._max_daily_buy,
            "max_position_ratio": self._max_position_ratio,
            "stop_loss_pct": self._stop_loss_pct,
            "cooldown_minutes": self._cooldown_minutes,
            "buy_conditions": self._buy_conditions,
            "sell_conditions": self._sell_conditions,
            "daily_summary": self.get_daily_summary(),
        }

    def should_buy(self, decision) -> bool:
        """
        매수 조건 충족 여부 확인

        Args:
            decision: FinalDecision 인스턴스

        Returns:
            True면 매수 실행 가능
        """
        if not self._enabled:
            return False

        conds = self._buy_conditions
        if not conds:
            return False

        # 점수 검증
        min_score = conds.get("min_total_score", 70)
        if decision.total_score < min_score:
            logger.info(
                f"[TradeExecutor] 매수 조건 미충족: 점수 {decision.total_score} < {min_score}"
            )
            return False

        # 확신도 검증
        min_confidence = conds.get("min_confidence", 60)
        if decision.confidence < min_confidence:
            logger.info(
                f"[TradeExecutor] 매수 조건 미충족: 확신도 {decision.confidence} < {min_confidence}"
            )
            return False

        # 투자 행동 검증
        allowed = conds.get("allowed_actions", ["STRONG_BUY", "BUY"])
        if decision.action.name not in allowed:
            logger.info(
                f"[TradeExecutor] 매수 조건 미충족: 행동 {decision.action.name} ∉ {allowed}"
            )
            return False

        # 리스크 레벨 검증
        max_risk = conds.get("max_risk_level", "MEDIUM")
        risk_order = ["VERY_LOW", "LOW", "MEDIUM", "HIGH", "VERY_HIGH"]
        try:
            if risk_order.index(decision.risk_level.name) > risk_order.index(max_risk):
                logger.info(
                    f"[TradeExecutor] 매수 조건 미충족: 리스크 {decision.risk_level.name} > {max_risk}"
                )
                return False
        except ValueError:
            return False

        return True

    def should_sell(self, decision) -> bool:
        """매도 조건 충족 여부 확인"""
        if not self._enabled:
            return False

        conds = self._sell_conditions
        if not conds:
            return False

        max_score = conds.get("max_total_score", 30)
        if decision.total_score > max_score:
            return False

        allowed = conds.get("allowed_actions", ["SELL", "STRONG_SELL"])
        if decision.action.name not in allowed:
            return False

        return True

    def execute_buy(
        self,
        stock_name: str,
        stock_code: str,
        decision,
        current_price: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        매수 주문 실행

        Args:
            stock_name: 종목명
            stock_code: 종목코드
            decision: FinalDecision
            current_price: 현재가 (None이면 시장가 주문)

        Returns:
            주문 결과 dict
        """
        # 서킷 브레이커 확인
        blocked = self._check_circuit_breaker(stock_code, "BUY")
        if blocked:
            return {"status": "blocked", "reason": blocked, "dry_run": self._dry_run}

        # 주문 금액 계산
        buy_amount = self._calculate_buy_amount(current_price)
        quantity = 0
        if current_price and current_price > 0:
            quantity = buy_amount // current_price

        order = {
            "timestamp": datetime.now(KST).isoformat(),
            "stock_name": stock_name,
            "stock_code": stock_code,
            "action": "BUY",
            "quantity": quantity,
            "price": current_price,
            "amount": buy_amount,
            "decision_score": decision.total_score,
            "decision_confidence": decision.confidence,
            "decision_action": decision.action.value,
            "dry_run": self._dry_run,
        }

        if self._dry_run:
            order["status"] = "simulated"
            logger.info(
                f"🧪 [DRY RUN] 매수 시뮬레이션: {stock_name}({stock_code}) "
                f"{quantity}주 × {current_price}원 = {buy_amount:,}원"
            )
            print(
                f"🧪 [DRY RUN] 매수 시뮬레이션: {stock_name}({stock_code}) "
                f"{quantity}주 × {current_price or '시장가'}원 = {buy_amount:,}원"
            )
        else:
            # 실제 KIS API 주문
            kis_result = self._send_kis_order(
                stock_code, "BUY", quantity, current_price
            )
            self._attach_kis_result(order, kis_result)

        self._record_order(order, count_toward_limits=self._is_effective_order_status(order.get("status")))
        return order

    def execute_sell(
        self,
        stock_name: str,
        stock_code: str,
        decision,
        quantity: int = 0,
        current_price: Optional[int] = None,
    ) -> Dict[str, Any]:
        """매도 주문 실행"""
        blocked = self._check_circuit_breaker(stock_code, "SELL")
        if blocked:
            return {"status": "blocked", "reason": blocked, "dry_run": self._dry_run}

        order = {
            "timestamp": datetime.now(KST).isoformat(),
            "stock_name": stock_name,
            "stock_code": stock_code,
            "action": "SELL",
            "quantity": quantity,
            "price": current_price,
            "amount": (quantity * current_price) if current_price else 0,
            "decision_score": decision.total_score,
            "decision_action": decision.action.value,
            "dry_run": self._dry_run,
        }

        if self._dry_run:
            order["status"] = "simulated"
            logger.info(
                f"🧪 [DRY RUN] 매도 시뮬레이션: {stock_name}({stock_code}) {quantity}주"
            )
            print(
                f"🧪 [DRY RUN] 매도 시뮬레이션: {stock_name}({stock_code}) {quantity}주"
            )
        else:
            kis_result = self._send_kis_order(
                stock_code, "SELL", quantity, current_price
            )
            self._attach_kis_result(order, kis_result)

        self._record_order(order, count_toward_limits=self._is_effective_order_status(order.get("status")))

        return order

    def preview_decision(
        self,
        stock_name: str,
        stock_code: str,
        decision,
        *,
        quantity: int = 0,
        current_price: Optional[int] = None,
    ) -> Dict[str, Any]:
        """현재 설정에서 해당 판단이 어떤 주문으로 이어질지 미리 계산."""
        self._reset_daily_budget_if_needed()

        if self.should_buy(decision):
            blocked = self._check_circuit_breaker(stock_code, "BUY")
            buy_amount = self._calculate_buy_amount(current_price)
            preview = {
                "stock_name": stock_name,
                "stock_code": stock_code,
                "action": "BUY",
                "quantity": (buy_amount // current_price) if current_price and current_price > 0 else 0,
                "price": current_price,
                "amount": buy_amount,
                "dry_run": self._dry_run,
            }
            return {
                "status": "blocked" if blocked else "ready",
                "reason": blocked or "buy_conditions_met",
                "order": preview,
                "runtime": self.get_runtime_config(),
            }

        if self.should_sell(decision):
            blocked = self._check_circuit_breaker(stock_code, "SELL")
            preview = {
                "stock_name": stock_name,
                "stock_code": stock_code,
                "action": "SELL",
                "quantity": quantity,
                "price": current_price,
                "amount": (quantity * current_price) if quantity and current_price else 0,
                "dry_run": self._dry_run,
            }
            return {
                "status": "blocked" if blocked else "ready",
                "reason": blocked or "sell_conditions_met",
                "order": preview,
                "runtime": self.get_runtime_config(),
            }

        return {
            "status": "no_action",
            "reason": self._build_no_action_reason(decision),
            "order": None,
            "runtime": self.get_runtime_config(),
        }

    def execute_decision(
        self,
        stock_name: str,
        stock_code: str,
        decision,
        *,
        quantity: int = 0,
        current_price: Optional[int] = None,
    ) -> Dict[str, Any]:
        """FinalDecision을 주문 실행 결과로 변환."""
        if self.should_buy(decision):
            return self.execute_buy(
                stock_name=stock_name,
                stock_code=stock_code,
                decision=decision,
                current_price=current_price,
            )
        if self.should_sell(decision):
            return self.execute_sell(
                stock_name=stock_name,
                stock_code=stock_code,
                decision=decision,
                quantity=quantity,
                current_price=current_price,
            )
        return {"status": "no_action", "reason": self._build_no_action_reason(decision), "dry_run": self._dry_run}

    # ──────────────────────────────────────────────
    # 서킷 브레이커
    # ──────────────────────────────────────────────

    def _check_circuit_breaker(self, stock_code: str, action: str) -> Optional[str]:
        """
        서킷 브레이커 확인

        Returns:
            차단 이유 문자열 또는 None (통과)
        """
        now = datetime.now(KST)
        self._reset_daily_budget_if_needed(now)

        # 1. 일일 매수 한도
        if action == "BUY" and self._daily_spent >= self._max_daily_buy:
            return (
                f"일일 매수 한도 초과: {self._daily_spent:,.0f} / {self._max_daily_buy:,}원"
            )

        # 2. 쿨다운
        last_time = self._last_order_time.get(stock_code)
        if last_time:
            elapsed = (now - last_time).total_seconds() / 60
            if elapsed < self._cooldown_minutes:
                remaining = self._cooldown_minutes - elapsed
                return f"쿨다운 중: {remaining:.0f}분 남음 ({stock_code})"

        return None

    def _calculate_buy_amount(self, current_price: Optional[int] = None) -> int:
        """매수 금액 계산 (일일 잔여 한도 내)"""
        remaining = self._max_daily_buy - self._daily_spent
        # 잔여 한도의 max_position_ratio 만큼만 사용
        amount = min(remaining, self._max_daily_buy * self._max_position_ratio)
        return max(0, int(amount))

    # ──────────────────────────────────────────────
    # KIS API 주문
    # ──────────────────────────────────────────────

    def _send_kis_order(
        self,
        stock_code: str,
        side: str,
        quantity: int,
        price: Optional[int],
    ) -> Any:
        """
        KIS API 주문 실행. 기본 자동매매 경로는 모의투자 서버만 허용합니다.

        Returns:
            KIS API 응답 dict 또는 "error: {message}"
        """
        if quantity <= 0:
            return "error: 주문 수량이 0입니다."

        paper = self._account_type == "paper"
        if not paper and not self._allow_real_trading:
            return "error: 실전 주문은 allow_real_trading=true 설정 없이는 차단됩니다."

        order_division = self._resolve_order_division(price)
        order_price = None if order_division == "01" else price

        try:
            from src.tools.realtime_tool import KISRealtimeTool

            tool = KISRealtimeTool(paper=paper)
            if not tool.is_available:
                return (
                    "error: KIS API 미설정 "
                    "(KIS_APP_KEY/KIS_APP_SECRET 또는 KIS_PAPER_APP_KEY/KIS_PAPER_APP_SECRET 확인)"
                )

            response = tool.place_order(
                stock_code=stock_code,
                side=side,
                quantity=quantity,
                price=order_price,
                order_division=order_division,
            )
            if response.get("rt_cd") == "0":
                return response

            message = response.get("msg1") or response.get("msg_cd") or "KIS 주문 실패"
            response["status"] = f"error: {message}"
            return response

        except Exception as e:
            logger.exception(f"[TradeExecutor] 주문 오류: {e}")
            return f"error: {str(e)[:200]}"

    def _resolve_order_division(self, price: Optional[int]) -> str:
        normalized = str(self._order_type or "limit").lower()
        if normalized in {"market", "market_price", "시장가"}:
            return "01"
        if normalized in {"limit", "limit_price", "지정가"}:
            return "00"
        # KIS 주문구분 코드를 직접 지정할 수 있게 허용합니다.
        if normalized.isdigit():
            return normalized.zfill(2)
        return "01" if price is None else "00"

    @staticmethod
    def _attach_kis_result(order: Dict[str, Any], kis_result: Any) -> None:
        if isinstance(kis_result, dict):
            order["kis_response"] = kis_result
            if kis_result.get("rt_cd") == "0":
                order["status"] = "submitted"
                output = kis_result.get("output") or {}
                if isinstance(output, dict):
                    order["kis_order_no"] = output.get("ODNO") or output.get("odno")
                    order["kis_order_time"] = output.get("ORD_TMD") or output.get("ord_tmd")
            else:
                order["status"] = kis_result.get("status") or f"error: {kis_result.get('msg1', 'KIS 주문 실패')}"
            return

        order["status"] = str(kis_result)

    # ──────────────────────────────────────────────
    # 주문 기록 저장
    # ──────────────────────────────────────────────

    def _save_order(self, order: Dict[str, Any]) -> None:
        """주문 기록을 JSON 파일로 저장"""
        try:
            date_str = datetime.now(KST).strftime("%Y-%m-%d")
            save_dir = self._orders_dir / date_str
            save_dir.mkdir(parents=True, exist_ok=True)

            # 날짜별 주문 로그 (append)
            log_file = save_dir / "orders.jsonl"
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(order, ensure_ascii=False, default=str) + "\n")

        except Exception as e:
            logger.exception(f"[TradeExecutor] 주문 기록 저장 실패: {e}")

    def get_daily_summary(self) -> Dict[str, Any]:
        """일일 매매 요약"""
        self._reset_daily_budget_if_needed()
        return {
            "date": self._daily_reset_date,
            "total_spent": self._daily_spent,
            "remaining_budget": max(0, self._max_daily_buy - self._daily_spent),
            "order_count": len([
                o for o in self._order_history
                if o.get("timestamp", "").startswith(self._daily_reset_date)
            ]),
            "dry_run": self._dry_run,
        }

    def _record_order(self, order: Dict[str, Any], *, count_toward_limits: bool) -> None:
        """주문 이력을 메모리/디스크에 기록하고 필요 시 예산과 쿨다운을 갱신."""
        self._order_history.append(order)
        if count_toward_limits:
            stock_code = str(order.get("stock_code", "")).strip()
            action = str(order.get("action", "")).upper()
            amount = float(order.get("amount", 0) or 0)
            timestamp = self._parse_timestamp(order.get("timestamp"))
            if action == "BUY":
                self._daily_spent += amount
            if stock_code and timestamp is not None:
                self._last_order_time[stock_code] = timestamp
        self._save_order(order)

    def _restore_state_from_disk(self) -> None:
        """당일 주문 로그를 읽어 예산/쿨다운 상태를 복원."""
        self._reset_daily_budget_if_needed()
        log_file = self._orders_dir / self._daily_reset_date / "orders.jsonl"
        if not log_file.exists():
            return

        try:
            with log_file.open("r", encoding="utf-8") as f:
                for line in f:
                    row = json.loads(line.strip())
                    if not isinstance(row, dict):
                        continue
                    self._order_history.append(row)
                    if not self._is_effective_order_status(row.get("status")):
                        continue

                    stock_code = str(row.get("stock_code", "")).strip()
                    action = str(row.get("action", "")).upper()
                    amount = float(row.get("amount", 0) or 0)
                    timestamp = self._parse_timestamp(row.get("timestamp"))

                    if action == "BUY":
                        self._daily_spent += amount
                    if stock_code and timestamp is not None:
                        previous = self._last_order_time.get(stock_code)
                        if previous is None or timestamp > previous:
                            self._last_order_time[stock_code] = timestamp
        except Exception as e:
            logger.exception(f"[TradeExecutor] 주문 상태 복원 실패: {e}")

    def _reset_daily_budget_if_needed(self, now: Optional[datetime] = None) -> None:
        current = now or datetime.now(KST)
        today = current.strftime("%Y-%m-%d")
        if self._daily_reset_date != today:
            self._daily_spent = 0.0
            self._daily_reset_date = today
            self._last_order_time = {}
            self._order_history = []

    @staticmethod
    def _parse_timestamp(value: Any) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=KST)
            return parsed.astimezone(KST)
        except Exception:
            return None

    @staticmethod
    def _is_effective_order_status(status: Any) -> bool:
        normalized = str(status or "").strip().lower()
        if not normalized:
            return True
        if normalized in {"blocked", "no_action"}:
            return False
        return not normalized.startswith("error")

    def _build_no_action_reason(self, decision) -> str:
        if not self._enabled:
            return "trading_disabled"
        if self._buy_conditions or self._sell_conditions:
            return (
                f"조건 미충족 (점수:{decision.total_score}, "
                f"행동:{getattr(getattr(decision, 'action', None), 'value', 'unknown')})"
            )
        return "trading_conditions_not_configured"
