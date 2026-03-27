# 파일: backend/api/routes/charts.py
"""
실시간 차트 WebSocket & 과거 캔들 REST 엔드포인트

WebSocket:
  WS /api/v1/charts/ws/{stock_code}
    → 클라이언트 메시지: {"action": "subscribe", "timeframe": "1m"}
    → 서버 메시지:
      - {"type": "candle_update", "timeframe": "1m", "data": {...}}
      - {"type": "tick", "data": {...}}

REST:
  GET /api/v1/charts/{stock_code}/history?timeframe=1m&count=200
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set

# KST 타임존 (UTC+9)
KST = timezone(timedelta(hours=9))

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from backend.services.candle_aggregator import Candle, CandleAggregator, TIMEFRAMES
from backend.services.kis_websocket_client import KISWebSocketClient, TickData
from backend.api.schemas import ErrorCode
from backend.api.errors import raise_api_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/charts", tags=["Charts"])


# ==========================================
# 응답 스키마
# ==========================================

class CandleResponse(BaseModel):
    """과거 캔들 응답"""
    stock_code: str
    timeframe: str
    candles: List[dict]
    has_more: bool = True


# ==========================================
# Chart WebSocket Manager (싱글톤)
# ==========================================

class ChartWebSocketManager:
    """
    실시간 차트 데이터 관리자

    역할:
    - 브라우저 WebSocket 클라이언트 관리
    - KIS WebSocket 연결 관리 (공유)
    - 틱 → 캔들 집계 후 브라우저에 브로드캐스트
    """

    _instance: Optional["ChartWebSocketManager"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        # 종목별 브라우저 클라이언트: {stock_code: {ws: set(timeframes)}}
        self._clients: Dict[str, Dict[WebSocket, Set[str]]] = {}

        # 종목별 캔들 집계기
        self._aggregators: Dict[str, CandleAggregator] = {}

        # KIS WebSocket 클라이언트 (공유)
        self._kis_client: Optional[KISWebSocketClient] = None
        self._kis_task: Optional[asyncio.Task] = None

        # 이벤트 루프 참조
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # ── KIS 연결 관리 ──

    async def _ensure_kis_connection(self):
        """KIS WebSocket 연결 보장"""
        if self._kis_client is None:
            self._kis_client = KISWebSocketClient(paper=False)
            self._kis_client.on_tick(self._on_kis_tick)

        if self._kis_task is None or self._kis_task.done():
            self._loop = asyncio.get_event_loop()
            self._kis_task = asyncio.create_task(self._kis_client.connect())
            logger.info("🚀 KIS WebSocket 백그라운드 태스크 시작")

    async def shutdown(self):
        """종료 시 정리"""
        if self._kis_client:
            await self._kis_client.disconnect()

        if self._kis_task and not self._kis_task.done():
            self._kis_task.cancel()
            try:
                await self._kis_task
            except asyncio.CancelledError:
                pass

        self._clients.clear()
        self._aggregators.clear()
        logger.info("🛑 ChartWebSocketManager 종료")

    # ── 클라이언트 관리 ──

    async def add_client(
        self, stock_code: str, timeframe: str, websocket: WebSocket
    ):
        """브라우저 클라이언트 등록"""
        # 클라이언트 등록
        if stock_code not in self._clients:
            self._clients[stock_code] = {}
        if websocket not in self._clients[stock_code]:
            self._clients[stock_code][websocket] = set()
        self._clients[stock_code][websocket].add(timeframe)

        # 집계기 생성
        if stock_code not in self._aggregators:
            self._aggregators[stock_code] = CandleAggregator()

        # KIS 구독 시작
        await self._ensure_kis_connection()
        await self._kis_client.subscribe(stock_code)

        client_count = sum(
            len(ws_set) for ws_set in self._clients.get(stock_code, {}).values()
        )
        logger.info(
            f"📊 클라이언트 등록: {stock_code}/{timeframe} "
            f"(현재 {client_count}명)"
        )

    async def remove_client(self, stock_code: str, websocket: WebSocket):
        """브라우저 클라이언트 제거"""
        if stock_code in self._clients:
            self._clients[stock_code].pop(websocket, None)

            # 해당 종목 클라이언트가 없으면 구독 해제
            if not self._clients[stock_code]:
                del self._clients[stock_code]
                self._aggregators.pop(stock_code, None)

                if self._kis_client:
                    await self._kis_client.unsubscribe(stock_code)

                logger.info(f"📊 종목 구독 해제: {stock_code} (클라이언트 없음)")

    # ── 틱 처리 & 브로드캐스트 ──

    def _on_kis_tick(self, tick: TickData):
        """
        KIS 틱 수신 콜백 (동기)

        KISWebSocketClient의 수신 루프에서 호출됨.
        asyncio 이벤트 루프에 브로드캐스트를 스케줄링합니다.
        """
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._process_tick(tick), self._loop
            )

    async def _process_tick(self, tick: TickData):
        """틱 처리: 집계 + 브로드캐스트"""
        stock_code = tick.stock_code
        aggregator = self._aggregators.get(stock_code)
        if not aggregator:
            return

        # 틱을 캔들로 집계
        completed_candles = aggregator.add_tick(tick)

        # 현재 미완성 캔들 (실시간 업데이트용)
        clients = self._clients.get(stock_code, {})
        if not clients:
            return

        # 1) 틱 브로드캐스트 (모든 클라이언트)
        tick_msg = json.dumps({
            "type": "tick",
            "data": {
                "stock_code": tick.stock_code,
                "price": tick.price,
                "volume": tick.volume,
                "cumulative_volume": tick.cumulative_volume,
                "change_rate": tick.change_rate,
                "trade_type": tick.trade_type,
                "timestamp": tick.timestamp.isoformat(),
                "open": tick.open_price,
                "high": tick.high_price,
                "low": tick.low_price,
            },
        }, ensure_ascii=False)

        await self._broadcast(stock_code, tick_msg)

        # 2) 완성된 캔들 브로드캐스트
        for tf, candle in completed_candles.items():
            if candle:
                candle_msg = json.dumps({
                    "type": "candle_complete",
                    "timeframe": tf,
                    "data": candle.to_dict(),
                }, ensure_ascii=False)
                await self._broadcast(stock_code, candle_msg, timeframe=tf)

        # 3) 현재 캔들 업데이트 (각 타임프레임별)
        for tf in TIMEFRAMES:
            current = aggregator.get_current_candle(tf)
            if current:
                update_msg = json.dumps({
                    "type": "candle_update",
                    "timeframe": tf,
                    "data": current.to_dict(),
                }, ensure_ascii=False)
                await self._broadcast(stock_code, update_msg, timeframe=tf)

    async def _broadcast(
        self, stock_code: str, message: str, timeframe: Optional[str] = None
    ):
        """메시지 브로드캐스트"""
        clients = self._clients.get(stock_code, {})
        dead_clients = []

        for ws, subscribed_tfs in clients.items():
            # 타임프레임 필터 (None이면 모든 클라이언트에 전송)
            if timeframe and timeframe not in subscribed_tfs:
                continue

            try:
                await ws.send_text(message)
            except Exception:
                dead_clients.append(ws)

        # 끊어진 클라이언트 정리
        for ws in dead_clients:
            await self.remove_client(stock_code, ws)


# 싱글톤 인스턴스
_manager = ChartWebSocketManager()


def get_chart_manager() -> ChartWebSocketManager:
    """ChartWebSocketManager 인스턴스 반환"""
    return _manager


# ==========================================
# WebSocket 엔드포인트
# ==========================================

@router.websocket("/ws/{stock_code}")
async def websocket_chart(websocket: WebSocket, stock_code: str):
    """
    실시간 차트 WebSocket

    연결 후 클라이언트가 구독 메시지를 보내면 실시간 데이터를 전송합니다.

    클라이언트 → 서버:
      {"action": "subscribe", "timeframe": "1m"}
      {"action": "unsubscribe", "timeframe": "1m"}

    서버 → 클라이언트:
      {"type": "tick", "data": {...}}
      {"type": "candle_update", "timeframe": "1m", "data": {...}}
      {"type": "candle_complete", "timeframe": "1m", "data": {...}}
      {"type": "error", "message": "..."}
    """
    # 종목코드 검증
    if not stock_code.isdigit() or len(stock_code) != 6:
        await websocket.close(code=4000, reason="종목코드는 6자리 숫자")
        return

    await websocket.accept()
    logger.info(f"🔗 WebSocket 연결: {stock_code}")

    manager = get_chart_manager()
    subscribed_timeframes: Set[str] = set()

    try:
        while True:
            # 클라이언트 메시지 수신
            raw = await websocket.receive_text()

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "잘못된 JSON"})
                continue

            action = msg.get("action")
            timeframe = msg.get("timeframe", "1m")

            # 타임프레임 검증
            if timeframe not in TIMEFRAMES:
                await websocket.send_json({
                    "type": "error",
                    "message": f"지원하지 않는 타임프레임: {timeframe}. "
                               f"가능한 값: {list(TIMEFRAMES.keys())}",
                })
                continue

            if action == "subscribe":
                subscribed_timeframes.add(timeframe)
                await manager.add_client(stock_code, timeframe, websocket)
                await websocket.send_json({
                    "type": "subscribed",
                    "stock_code": stock_code,
                    "timeframe": timeframe,
                })

            elif action == "unsubscribe":
                subscribed_timeframes.discard(timeframe)
                # 클라이언트의 해당 타임프레임만 제거
                clients = manager._clients.get(stock_code, {})
                if websocket in clients:
                    clients[websocket].discard(timeframe)

                await websocket.send_json({
                    "type": "unsubscribed",
                    "timeframe": timeframe,
                })

            elif action == "ping":
                await websocket.send_json({"type": "pong"})

            else:
                await websocket.send_json({
                    "type": "error",
                    "message": f"알 수 없는 action: {action}",
                })

    except WebSocketDisconnect:
        logger.info(f"🔌 WebSocket 연결 종료: {stock_code}")
    except Exception as e:
        logger.error(f"WebSocket 오류: {e}")
    finally:
        await manager.remove_client(stock_code, websocket)


# ==========================================
# 유틸: 1분봉 → N분봉 집계
# ==========================================

def _aggregate_candles(candles_1m: List[dict], interval_seconds: int) -> List[dict]:
    """
    1분봉 리스트를 N분봉으로 집계합니다.

    Args:
        candles_1m: 시간순 정렬된 1분봉 리스트 [{time, open, high, low, close, volume}, ...]
        interval_seconds: 목표 타임프레임 (초 단위, e.g. 300 = 5분)

    Returns:
        집계된 캔들 리스트 (시간순)
    """
    if not candles_1m:
        return []

    aggregated: List[dict] = []
    bucket_start: Optional[int] = None
    bucket: Optional[dict] = None

    for c in candles_1m:
        ts = c["time"]
        # 버킷 시작 시간 = timestamp를 interval로 내림
        floored = (ts // interval_seconds) * interval_seconds

        if bucket is None or floored != bucket_start:
            # 이전 버킷 저장
            if bucket is not None:
                aggregated.append(bucket)

            # 새 버킷 시작
            bucket_start = floored
            bucket = {
                "time": floored,
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c["volume"],
            }
        else:
            # 기존 버킷에 병합
            bucket["high"] = max(bucket["high"], c["high"])
            bucket["low"] = min(bucket["low"], c["low"])
            bucket["close"] = c["close"]
            bucket["volume"] += c["volume"]

    # 마지막 버킷
    if bucket is not None:
        aggregated.append(bucket)

    return aggregated


# ==========================================
# 키움 ka10080 기반 캔들 조회
# ==========================================

async def _get_candles_kiwoom(
    stock_code: str, timeframe: str, count: int, before: Optional[int]
) -> CandleResponse:
    """
    키움 REST API ka10080으로 분봉 데이터 조회

    키움 REST API는 1회 호출 시 최대 900개의 분봉 캔들을 반환합니다.
    연속조회(next=2)는 동일 데이터를 반환하므로, 단일 요청으로 처리합니다.

    데이터 커버리지 (900개 기준):
    - 1분: ~2.3 거래일
    - 5분: ~11.5 거래일
    - 15분: ~34 거래일
    - 1시간: ~6.5 개월
    """
    from backend.services.kiwoom_client import (
        get_minute_chart,
        TIMEFRAME_TO_TIC_SCOPE,
    )

    tic_scope = TIMEFRAME_TO_TIC_SCOPE[timeframe]

    try:
        # 키움 API 단일 요청 (최대 900개)
        all_candles, _, _ = await get_minute_chart(
            stock_code=stock_code,
            tic_scope=tic_scope,
            count=900,
            next_key="0",
        )

        # before 필터: 해당 타임스탬프 미만의 캔들만 유지
        if before is not None:
            all_candles = [c for c in all_candles if c["time"] < before]

        # 마지막 count개 반환 (가장 최신 쪽)
        has_more = len(all_candles) > count
        result_candles = all_candles[-count:] if len(all_candles) > count else all_candles

        # 데이터가 비어있거나 count보다 적으면 더 이상 없음
        if len(result_candles) == 0 or len(result_candles) < count:
            has_more = False

        logger.info(
            f"📊 키움 캔들 응답: stock={stock_code} tf={timeframe} "
            f"before={before} total={len(all_candles)} "
            f"result={len(result_candles)} has_more={has_more}"
        )

        return CandleResponse(
            stock_code=stock_code,
            timeframe=timeframe,
            candles=result_candles,
            has_more=has_more,
        )

    except Exception as e:
        logger.error(f"키움 캔들 조회 실패: {e}", exc_info=True)
        raise_api_error(
            ErrorCode.CHART_LOAD_FAILED,
            "차트 데이터 조회에 실패했습니다.",
            detail=str(e),
        )


# ==========================================
# REST 엔드포인트: 과거 캔들 데이터
# ==========================================

@router.get("/{stock_code}/history", response_model=CandleResponse)
async def get_historical_candles(
    stock_code: str,
    timeframe: str = Query(
        default="1m",
        description="타임프레임 (1m, 3m, 5m, 15m, 30m, 1h)",
    ),
    count: int = Query(
        default=200,
        ge=1,
        le=1000,
        description="캔들 수 (최대 1000)",
    ),
    before: Optional[int] = Query(
        default=None,
        description="이 UNIX timestamp(초) 이전의 캔들만 반환 (페이지네이션용)",
    ),
):
    """
    과거 캔들 데이터 조회

    키움 REST API (ka10080)를 사용하여 분봉 차트 데이터를 조회합니다.
    차트 초기 로딩 시 사용합니다.
    """
    # 종목코드 검증
    if not stock_code.isdigit() or len(stock_code) != 6:
        raise_api_error(
            ErrorCode.STOCK_INVALID_CODE,
            "종목코드는 6자리 숫자여야 합니다.",
        )

    # 타임프레임 검증
    if timeframe not in TIMEFRAMES:
        raise_api_error(
            ErrorCode.INVALID_REQUEST,
            f"지원하지 않는 타임프레임: {timeframe}. 가능한 값: {list(TIMEFRAMES.keys())}",
        )

    # 키움 API 설정 확인
    from backend.services.kiwoom_client import (
        is_kiwoom_available,
        TIMEFRAME_TO_TIC_SCOPE,
    )

    if not is_kiwoom_available():
        raise_api_error(
            ErrorCode.CHART_API_NOT_CONFIGURED,
            "키움 API가 설정되지 않았습니다. KIWOOM_APP_KEY / KIWOOM_APP_SECRET을 확인하세요.",
        )

    if timeframe not in TIMEFRAME_TO_TIC_SCOPE:
        raise_api_error(
            ErrorCode.INVALID_REQUEST,
            f"지원하지 않는 타임프레임: {timeframe}",
        )

    return await _get_candles_kiwoom(stock_code, timeframe, count, before)
