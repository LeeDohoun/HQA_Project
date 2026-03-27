# 파일: backend/services/candle_aggregator.py
"""
실시간 틱 → OHLCV 캔들 변환기

KIS WebSocket에서 수신한 체결 데이터(TickData)를
여러 타임프레임(1m, 3m, 5m, 15m, 30m, 1h)의 캔들로 집계합니다.

사용 예:
    aggregator = CandleAggregator()
    completed = aggregator.add_tick(tick)
    # completed = {"1m": Candle(...), "3m": None, ...}
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from backend.services.kis_websocket_client import TickData

logger = logging.getLogger(__name__)


# ==========================================
# 캔들 모델
# ==========================================

@dataclass
class Candle:
    """OHLCV 캔들"""
    timestamp: datetime   # 캔들 시작 시각
    open: float
    high: float
    low: float
    close: float
    volume: int
    complete: bool = False  # True면 완성된 캔들

    def to_dict(self) -> dict:
        """JSON 직렬화용 딕셔너리"""
        return {
            "timestamp": self.timestamp.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "complete": self.complete,
        }

    def to_lightweight_chart(self) -> dict:
        """
        lightweight-charts 형식으로 변환

        lightweight-charts는 time을 UNIX timestamp (초)로 받습니다.
        """
        return {
            "time": int(self.timestamp.timestamp()),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
        }

    def to_volume_data(self) -> dict:
        """lightweight-charts 볼륨 바 형식"""
        color = "rgba(38, 166, 154, 0.5)" if self.close >= self.open else "rgba(239, 83, 80, 0.5)"
        return {
            "time": int(self.timestamp.timestamp()),
            "value": self.volume,
            "color": color,
        }


# ==========================================
# 타임프레임 버킷
# ==========================================

# 지원 타임프레임 (초 단위)
TIMEFRAMES: Dict[str, int] = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "10m": 600,
    "15m": 900,
    "30m": 1800,
    "45m": 2700,
    "1h": 3600,
}


def _floor_to_interval(dt: datetime, interval_seconds: int) -> datetime:
    """
    타임스탬프를 타임프레임 간격으로 내림

    예: 09:32:45, interval=180(3m) → 09:30:00
        09:47:12, interval=900(15m) → 09:45:00
    """
    # 자정 기준으로 초 계산
    midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    seconds_since_midnight = int((dt - midnight).total_seconds())
    floored_seconds = (seconds_since_midnight // interval_seconds) * interval_seconds
    return midnight + timedelta(seconds=floored_seconds)


class _TimeframeBucket:
    """
    단일 타임프레임 캔들 빌더

    틱을 받아서 OHLCV 캔들을 구성합니다.
    타임프레임 경계를 넘으면 완성된 캔들을 반환합니다.
    """

    def __init__(self, interval_seconds: int):
        self.interval = interval_seconds
        self._current: Optional[Candle] = None

    @property
    def current_candle(self) -> Optional[Candle]:
        """현재 미완성 캔들"""
        return self._current

    def add_tick(self, tick: TickData) -> Optional[Candle]:
        """
        틱 추가. 캔들 경계를 넘으면 완성된 캔들 반환.

        Args:
            tick: 체결 데이터

        Returns:
            완성된 캔들 (경계를 넘었을 때) 또는 None
        """
        candle_start = _floor_to_interval(tick.timestamp, self.interval)
        completed_candle = None

        if self._current is None:
            # 첫 틱 → 새 캔들 시작
            self._current = Candle(
                timestamp=candle_start,
                open=tick.price,
                high=tick.price,
                low=tick.price,
                close=tick.price,
                volume=tick.volume,
            )
        elif candle_start > self._current.timestamp:
            # 새 캔들 구간 진입 → 이전 캔들 완성
            self._current.complete = True
            completed_candle = self._current

            # 새 캔들 시작
            self._current = Candle(
                timestamp=candle_start,
                open=tick.price,
                high=tick.price,
                low=tick.price,
                close=tick.price,
                volume=tick.volume,
            )
        else:
            # 같은 캔들 구간 → 업데이트
            self._current.high = max(self._current.high, tick.price)
            self._current.low = min(self._current.low, tick.price)
            self._current.close = tick.price
            self._current.volume += tick.volume

        return completed_candle

    def reset(self):
        """버킷 초기화"""
        self._current = None


# ==========================================
# 캔들 집계기
# ==========================================

class CandleAggregator:
    """
    멀티 타임프레임 캔들 집계기

    하나의 틱을 받으면 모든 타임프레임에 대해 캔들을 업데이트하고,
    완성된 캔들이 있으면 반환합니다.
    """

    def __init__(self):
        self._buckets: Dict[str, _TimeframeBucket] = {
            tf: _TimeframeBucket(seconds)
            for tf, seconds in TIMEFRAMES.items()
        }

    def add_tick(self, tick: TickData) -> Dict[str, Optional[Candle]]:
        """
        틱 추가. 각 타임프레임별 완성된 캔들 반환.

        Args:
            tick: 체결 데이터

        Returns:
            타임프레임별 완성된 캔들 딕셔너리
            예: {"1m": Candle(...), "3m": None, "5m": None, ...}
        """
        result: Dict[str, Optional[Candle]] = {}

        for tf, bucket in self._buckets.items():
            completed = bucket.add_tick(tick)
            result[tf] = completed

        return result

    def get_current_candle(self, timeframe: str) -> Optional[Candle]:
        """현재 미완성 캔들 조회"""
        bucket = self._buckets.get(timeframe)
        if bucket:
            return bucket.current_candle
        return None

    def get_all_current_candles(self) -> Dict[str, Optional[Candle]]:
        """모든 타임프레임의 현재 미완성 캔들"""
        return {
            tf: bucket.current_candle
            for tf, bucket in self._buckets.items()
        }

    def reset(self):
        """모든 버킷 초기화"""
        for bucket in self._buckets.values():
            bucket.reset()
