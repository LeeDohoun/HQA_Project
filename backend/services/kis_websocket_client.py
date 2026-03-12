# 파일: backend/services/kis_websocket_client.py
"""
한국투자증권 WebSocket 실시간 시세 클라이언트

KIS Open API WebSocket을 통해 실시간 체결가(H0STCNT0) 데이터를 수신합니다.

구조:
- KISApprovalKeyManager: WebSocket 접속키 발급/관리
- KISWebSocketClient: WebSocket 연결, 구독, 수신, 재연결
- TickData: 체결 데이터 모델

참고:
- KIS WebSocket은 계정당 1개 연결만 허용
- H0STCNT0 + H0STASP0 합산 최대 20개 종목 구독
- 수신 데이터는 pipe(|) + caret(^) 구분자 형식
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Callable, Dict, List, Optional, Set

import requests
import websockets
from websockets.exceptions import ConnectionClosed
from websockets.protocol import State as WSState

# KIS 설정 재사용
from src.utils.kis_auth import KISConfig, is_api_available

logger = logging.getLogger(__name__)


# ==========================================
# 데이터 모델
# ==========================================

@dataclass
class TickData:
    """실시간 체결 데이터"""
    stock_code: str
    timestamp: datetime       # 체결 시각
    price: float              # 현재가
    prev_close_diff: float    # 전일 대비
    change_rate: float        # 등락률 (%)
    volume: int               # 체결 거래량
    cumulative_volume: int    # 누적 거래량
    trade_type: str           # "BUY" | "SELL"
    open_price: float         # 시가
    high_price: float         # 고가
    low_price: float          # 저가


# ==========================================
# H0STCNT0 필드 파서
# ==========================================

# KIS H0STCNT0 응답 46개 필드 (^ 구분)
# 주요 필드 인덱스:
#  0: MKSC_SHRN_ISCD  - 유가증권 단축 종목코드
#  1: STCK_CNTG_HOUR  - 주식 체결 시간 (HHMMSS)
#  2: STCK_PRPR       - 주식 현재가
#  3: PRDY_VRSS_SIGN  - 전일 대비 부호 (1:상한, 2:상승, 3:보합, 4:하한, 5:하락)
#  4: PRDY_VRSS       - 전일 대비
#  5: PRDY_CTRT       - 전일 대비율
#  7: STCK_OPRC       - 시가
#  8: STCK_HGPR       - 고가
#  9: STCK_LWPR       - 저가
# 12: CNTG_VOL        - 체결 거래량
# 13: ACML_VOL        - 누적 거래량
# 20: CCLD_DVSN       - 체결구분 (1:매수, 5:매도)

def parse_h0stcnt0(stock_code: str, fields: List[str]) -> Optional[TickData]:
    """
    H0STCNT0 체결가 데이터 파싱

    Args:
        stock_code: 종목코드
        fields: ^ 구분된 필드 리스트 (46개)

    Returns:
        TickData 또는 None (파싱 실패 시)
    """
    try:
        if len(fields) < 21:
            logger.warning(f"H0STCNT0 필드 수 부족: {len(fields)}")
            return None

        # 체결 시간 파싱 (HHMMSS)
        time_str = fields[1].strip()
        now = datetime.now()
        hour = int(time_str[:2])
        minute = int(time_str[2:4])
        second = int(time_str[4:6])
        tick_time = now.replace(hour=hour, minute=minute, second=second, microsecond=0)

        # 체결구분: 1=매수, 5=매도
        trade_type_code = fields[20].strip() if len(fields) > 20 else "0"
        trade_type = "BUY" if trade_type_code == "1" else "SELL"

        return TickData(
            stock_code=stock_code,
            timestamp=tick_time,
            price=float(fields[2]),
            prev_close_diff=float(fields[4]),
            change_rate=float(fields[5]),
            volume=int(fields[12]),
            cumulative_volume=int(fields[13]),
            trade_type=trade_type,
            open_price=float(fields[7]),
            high_price=float(fields[8]),
            low_price=float(fields[9]),
        )
    except (ValueError, IndexError) as e:
        logger.error(f"H0STCNT0 파싱 오류: {e}, fields={fields[:5]}...")
        return None


# ==========================================
# Approval Key 관리
# ==========================================

class KISApprovalKeyManager:
    """
    WebSocket 접속키(approval_key) 발급 및 관리

    REST 토큰과 별도로 WebSocket 전용 접속키를 발급받아야 합니다.
    POST /oauth2/Approval → {approval_key: "..."}
    """

    _instance: Optional["KISApprovalKeyManager"] = None
    _approval_key: Optional[str] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def get_approval_key(self, paper: bool = False) -> str:
        """
        Approval key 발급 (캐시된 키가 있으면 반환)

        Note: KIS approval key는 WebSocket 연결 시마다 새로 발급 권장.
              하지만 동일 세션 내에서는 재사용 가능.
        """
        if self._approval_key:
            return self._approval_key

        return self._issue_approval_key(paper)

    def _issue_approval_key(self, paper: bool = False) -> str:
        """REST API로 approval key 발급"""
        domain = KISConfig.VTS_DOMAIN if paper else KISConfig.PROD_DOMAIN
        url = f"{domain}/oauth2/Approval"

        headers = {"content-type": "application/json"}
        body = {
            "grant_type": "client_credentials",
            "appkey": KISConfig.APP_KEY,
            "secretkey": KISConfig.APP_SECRET,
        }

        try:
            resp = requests.post(url, headers=headers, json=body, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            self._approval_key = data.get("approval_key", "")
            logger.info("✅ KIS WebSocket approval key 발급 완료")
            return self._approval_key

        except Exception as e:
            logger.error(f"❌ Approval key 발급 실패: {e}")
            return ""

    def invalidate(self):
        """캐시된 키 무효화 (재연결 시 사용)"""
        self._approval_key = None


# ==========================================
# WebSocket 클라이언트
# ==========================================

class KISWebSocketClient:
    """
    KIS WebSocket 클라이언트

    - 실시간 체결가(H0STCNT0) 수신
    - 자동 재연결 (지수 백오프)
    - 콜백 기반 데이터 전달
    """

    # WebSocket URL
    PROD_WS_URL = "ws://ops.koreainvestment.com:21000"
    PAPER_WS_URL = "ws://ops.koreainvestment.com:31000"

    # 구독 제한
    MAX_SUBSCRIPTIONS = 20

    def __init__(self, paper: bool = False):
        self._paper = paper
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._approval_mgr = KISApprovalKeyManager()
        self._subscribed_stocks: Set[str] = set()
        self._running = False
        self._reconnect_delay = 1.0  # 초기 재연결 대기 (초)
        self._max_reconnect_delay = 60.0
        self._tick_callbacks: List[Callable[[TickData], None]] = []
        self._receive_task: Optional[asyncio.Task] = None

    @property
    def ws_url(self) -> str:
        return self.PAPER_WS_URL if self._paper else self.PROD_WS_URL

    @property
    def is_connected(self) -> bool:
        if self._ws is None:
            return False
        try:
            return self._ws.state is WSState.OPEN
        except AttributeError:
            # websockets 버전 호환
            return getattr(self._ws, 'open', False)

    @property
    def subscribed_stocks(self) -> Set[str]:
        return self._subscribed_stocks.copy()

    def on_tick(self, callback: Callable[[TickData], None]):
        """틱 수신 콜백 등록"""
        self._tick_callbacks.append(callback)

    def remove_tick_callback(self, callback: Callable[[TickData], None]):
        """틱 수신 콜백 제거"""
        if callback in self._tick_callbacks:
            self._tick_callbacks.remove(callback)

    # ── 연결 관리 ──

    async def connect(self):
        """KIS WebSocket 연결"""
        if not is_api_available():
            logger.error("KIS API 키가 설정되지 않았습니다.")
            return

        self._running = True
        self._reconnect_delay = 1.0

        while self._running:
            try:
                # Approval key 발급
                approval_key = self._approval_mgr.get_approval_key(self._paper)
                if not approval_key:
                    logger.error("Approval key를 받을 수 없습니다. 재시도...")
                    await asyncio.sleep(self._reconnect_delay)
                    self._approval_mgr.invalidate()
                    continue

                logger.info(f"🔌 KIS WebSocket 연결 중: {self.ws_url}")
                self._ws = await websockets.connect(
                    self.ws_url,
                    ping_interval=30,
                    ping_timeout=10,
                    close_timeout=5,
                )
                logger.info("✅ KIS WebSocket 연결 성공")
                self._reconnect_delay = 1.0  # 성공하면 리셋

                # 기존 구독 복원
                for stock_code in list(self._subscribed_stocks):
                    await self._send_subscribe(stock_code, approval_key)

                # 수신 루프
                await self._receive_loop()

            except ConnectionClosed as e:
                logger.warning(f"⚠️ KIS WebSocket 연결 끊김: {e}")
            except Exception as e:
                logger.error(f"❌ KIS WebSocket 오류: {e}")
            finally:
                self._ws = None

            if not self._running:
                break

            # 재연결 대기 (지수 백오프)
            logger.info(f"🔄 {self._reconnect_delay}초 후 재연결...")
            await asyncio.sleep(self._reconnect_delay)
            self._reconnect_delay = min(
                self._reconnect_delay * 2,
                self._max_reconnect_delay,
            )
            self._approval_mgr.invalidate()  # 재연결 시 새 키 발급

    async def disconnect(self):
        """연결 종료"""
        self._running = False
        if self._ws and self.is_connected:
            # 모든 구독 해제
            approval_key = self._approval_mgr.get_approval_key(self._paper)
            for stock_code in list(self._subscribed_stocks):
                await self._send_unsubscribe(stock_code, approval_key)

            await self._ws.close()
            logger.info("🔌 KIS WebSocket 연결 종료")

        self._ws = None
        self._subscribed_stocks.clear()

    # ── 구독 관리 ──

    async def subscribe(self, stock_code: str):
        """종목 실시간 체결가 구독"""
        if stock_code in self._subscribed_stocks:
            logger.info(f"이미 구독 중: {stock_code}")
            return

        if len(self._subscribed_stocks) >= self.MAX_SUBSCRIPTIONS:
            logger.warning(f"구독 한도 초과 (최대 {self.MAX_SUBSCRIPTIONS})")
            return

        self._subscribed_stocks.add(stock_code)

        if self.is_connected:
            approval_key = self._approval_mgr.get_approval_key(self._paper)
            await self._send_subscribe(stock_code, approval_key)

    async def unsubscribe(self, stock_code: str):
        """종목 구독 해제"""
        if stock_code not in self._subscribed_stocks:
            return

        self._subscribed_stocks.discard(stock_code)

        if self.is_connected:
            approval_key = self._approval_mgr.get_approval_key(self._paper)
            await self._send_unsubscribe(stock_code, approval_key)

    async def _send_subscribe(self, stock_code: str, approval_key: str):
        """구독 메시지 전송"""
        msg = {
            "header": {
                "approval_key": approval_key,
                "custtype": "P",
                "tr_type": "1",  # 1 = 등록
                "content-type": "utf-8",
            },
            "body": {
                "input": {
                    "tr_id": "H0STCNT0",
                    "tr_key": stock_code,
                }
            },
        }
        await self._ws.send(json.dumps(msg))
        logger.info(f"📡 구독 요청: H0STCNT0 / {stock_code}")

    async def _send_unsubscribe(self, stock_code: str, approval_key: str):
        """구독 해제 메시지 전송"""
        msg = {
            "header": {
                "approval_key": approval_key,
                "custtype": "P",
                "tr_type": "2",  # 2 = 해제
                "content-type": "utf-8",
            },
            "body": {
                "input": {
                    "tr_id": "H0STCNT0",
                    "tr_key": stock_code,
                }
            },
        }
        await self._ws.send(json.dumps(msg))
        logger.info(f"📡 구독 해제: H0STCNT0 / {stock_code}")

    # ── 수신 처리 ──

    async def _receive_loop(self):
        """WebSocket 수신 루프"""
        async for raw_data in self._ws:
            try:
                # 바이너리 → 문자열 디코딩
                if isinstance(raw_data, bytes):
                    raw_data = raw_data.decode("utf-8")

                # JSON 응답 (구독 확인 등)
                if raw_data.startswith("{"):
                    resp = json.loads(raw_data)
                    header = resp.get("header", {})
                    tr_id = header.get("tr_id", "")
                    msg_cd = header.get("msg_cd", "")
                    msg1 = resp.get("body", {}).get("msg1", "")
                    logger.info(f"📨 KIS 응답: tr_id={tr_id}, msg_cd={msg_cd}, msg={msg1}")
                    continue

                # 실시간 데이터: 첫 글자 "0" 또는 "1"
                if raw_data and raw_data[0] in ("0", "1"):
                    self._process_realtime_data(raw_data)

            except Exception as e:
                logger.error(f"수신 데이터 처리 오류: {e}")

    def _process_realtime_data(self, raw_data: str):
        """
        실시간 데이터 파싱

        형식: 0|H0STCNT0|001|005930^field1^field2^...^field46
        """
        parts = raw_data.split("|")
        if len(parts) < 4:
            return

        tr_id = parts[1]  # e.g., "H0STCNT0"
        data_count = int(parts[2])  # 데이터 건수
        body = parts[3]  # 종목코드^필드1^필드2^...

        if tr_id != "H0STCNT0":
            return

        # ^ 구분자로 분리
        fields = body.split("^")

        # 46개 필드씩 처리 (다중 종목 가능)
        field_count = 46
        for i in range(data_count):
            start = i * field_count
            chunk = fields[start : start + field_count]

            if len(chunk) < 21:
                continue

            stock_code = chunk[0].strip()
            tick = parse_h0stcnt0(stock_code, chunk)

            if tick:
                # 등록된 콜백에 전달
                for callback in self._tick_callbacks:
                    try:
                        callback(tick)
                    except Exception as e:
                        logger.error(f"콜백 오류: {e}")
