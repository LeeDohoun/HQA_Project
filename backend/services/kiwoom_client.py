# 파일: backend/services/kiwoom_client.py
"""
키움증권 REST API 클라이언트

ka10080 (주식분봉차트조회)를 통해 분봉 데이터를 조회합니다.

특징:
  - 틱범위 직접 지정 가능: 1, 3, 5, 10, 15, 30, 45, 60분
  - 1회 요청당 최대 900개 캔들 반환
  - 약 8개월(160 거래일) 이전까지 조회 가능
  - 연속조회(next=2) 지원으로 페이지네이션 가능

인증:
  - OAuth2 client_credentials 방식
  - 토큰은 Redis에 캐싱 (키: kiwoom:access_token, TTL: 만료시간)
  - Redis에 토큰이 없으면 새로 발급 후 저장
  - .env에 KIWOOM_APP_KEY, KIWOOM_APP_SECRET 설정 필요

API 문서: https://openapi.kiwoom.com/guide/apiguide
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

KST = timezone(timedelta(hours=9))

# ==========================================
# 설정
# ==========================================

KIWOOM_BASE_URL = os.getenv("KIWOOM_BASE_URL", "https://api.kiwoom.com")
KIWOOM_APP_KEY = os.getenv("KIWOOM_APP_KEY", "")
KIWOOM_APP_SECRET = os.getenv("KIWOOM_APP_SECRET", "")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Redis 키
REDIS_TOKEN_KEY = "kiwoom:access_token"

# 지원하는 틱범위
VALID_TIC_SCOPES = {"1", "3", "5", "10", "15", "30", "45", "60"}


# ==========================================
# Redis 연결
# ==========================================

def _get_redis():
    """Redis 클라이언트 반환 (lazy init)"""
    try:
        import redis
        return redis.from_url(REDIS_URL, decode_responses=True)
    except Exception as e:
        logger.warning(f"Redis 연결 실패: {e}")
        return None


# ==========================================
# 토큰 관리 (Redis 기반)
# ==========================================

class KiwoomAuth:
    """키움 OAuth2 토큰 관리 (Redis 캐싱)"""

    @property
    def is_available(self) -> bool:
        """API 키가 설정되어 있는지 확인"""
        return bool(KIWOOM_APP_KEY and KIWOOM_APP_SECRET)

    def _load_token_from_redis(self) -> Optional[str]:
        """Redis에서 토큰 조회"""
        try:
            r = _get_redis()
            if r is None:
                return None
            token = r.get(REDIS_TOKEN_KEY)
            if token:
                logger.debug("Redis에서 키움 토큰 로드 성공")
            return token
        except Exception as e:
            logger.warning(f"Redis 토큰 로드 실패: {e}")
            return None

    def _save_token_to_redis(self, token: str, ttl_seconds: int):
        """Redis에 토큰 저장 (TTL 설정)"""
        try:
            r = _get_redis()
            if r is None:
                return
            r.setex(REDIS_TOKEN_KEY, ttl_seconds, token)
            logger.debug(f"Redis에 키움 토큰 저장 (TTL: {ttl_seconds}초)")
        except Exception as e:
            logger.warning(f"Redis 토큰 저장 실패: {e}")

    async def get_token(self) -> str:
        """
        유효한 액세스 토큰 반환

        1. Redis에서 토큰 조회
        2. 없으면 키움 API에서 새 토큰 발급
        3. 발급된 토큰을 Redis에 저장 (만료 시간 TTL)
        """
        # 1) Redis에서 캐시된 토큰 확인
        cached = self._load_token_from_redis()
        if cached:
            return cached

        # 2) 새 토큰 발급
        return await self._issue_token()

    async def _issue_token(self) -> str:
        """
        새 액세스 토큰 발급

        POST /oauth2/token
        Body: { "grant_type": "client_credentials", "appkey": "...", "secretkey": "..." }
        """
        if not self.is_available:
            raise RuntimeError("KIWOOM_APP_KEY / KIWOOM_APP_SECRET 가 설정되지 않았습니다.")

        url = f"{KIWOOM_BASE_URL}/oauth2/token"
        body = {
            "grant_type": "client_credentials",
            "appkey": KIWOOM_APP_KEY,
            "secretkey": KIWOOM_APP_SECRET,
        }

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                json=body,
                headers={"Content-Type": "application/json;charset=UTF-8"},
            )
            resp.raise_for_status()
            data = resp.json()

        access_token = data.get("token") or data.get("access_token")
        if not access_token:
            raise RuntimeError(f"키움 토큰 발급 실패: {data}")

        # 토큰 만료 시간 (기본 24시간, 1분 여유)
        expires_in = int(data.get("expires_in", 86400))
        ttl = max(expires_in - 60, 60)

        # 3) Redis에 저장
        self._save_token_to_redis(access_token, ttl)

        logger.info(f"✅ 키움 토큰 발급 완료 (TTL: {ttl}초)")
        return access_token


# 싱글톤 인증 인스턴스
_auth = KiwoomAuth()


# ==========================================
# ka10080 - 주식분봉차트조회
# ==========================================

async def get_minute_chart(
    stock_code: str,
    tic_scope: str = "1",
    count: int = 900,
    next_key: str = "0",
) -> Tuple[List[dict], bool, str]:
    """
    키움 ka10080 주식분봉차트조회

    Args:
        stock_code: 종목코드 (6자리, e.g. "005930")
        tic_scope: 틱범위 ("1"=1분, "3"=3분, "5"=5분, "10"=10분,
                   "15"=15분, "30"=30분, "45"=45분, "60"=60분)
        count: 요청 캔들 수 (최대 900)
        next_key: 연속조회 키 ("0"=처음, "2"=연속)

    Returns:
        (candles, has_more, next_key)
        - candles: [{time, open, high, low, close, volume}, ...]
        - has_more: 연속 데이터 존재 여부
        - next_key: 다음 요청에 사용할 키 ("2" 또는 "0")
    """
    if tic_scope not in VALID_TIC_SCOPES:
        raise ValueError(f"지원하지 않는 틱범위: {tic_scope}. 가능: {VALID_TIC_SCOPES}")

    token = await _auth.get_token()

    url = f"{KIWOOM_BASE_URL}/api/dostk/chart"
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "authorization": f"Bearer {token}",
        "api-id": "ka10080",
    }
    body = {
        "stk_cd": stock_code,
        "tic_scope": tic_scope,
        "upd_stkpc_tp": "1",  # 수정주가 적용
        "next": next_key,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()

    # 응답 코드 확인
    return_code = data.get("return_code", data.get("rt_cd", ""))
    if str(return_code) != "0" and str(return_code) != "":
        msg = data.get("return_msg", data.get("msg1", "알 수 없는 오류"))
        logger.error(f"키움 ka10080 오류: {return_code} - {msg}")
        raise RuntimeError(f"키움 API 오류: {msg}")

    # 차트 데이터 파싱
    # 응답 구조: stk_min_pole_chart_qry 배열에 캔들 데이터
    # 필드: cntr_tm(체결시간), open_pric(시가), high_pric(고가),
    #       low_pric(저가), cur_prc(현재가), trde_qty(거래량)
    raw_list = (
        data.get("stk_min_pole_chart_qry")
        or data.get("output")
        or data.get("chart")
        or []
    )
    candles = []

    for item in raw_list:
        try:
            # 체결시간: YYYYMMDDHHmmss 형식
            time_str = str(
                item.get("cntr_tm")
                or item.get("체결시간")
                or item.get("stck_cntg_hour")
                or ""
            )
            if len(time_str) >= 12:
                dt = datetime.strptime(time_str[:12], "%Y%m%d%H%M").replace(tzinfo=KST)
            elif len(time_str) >= 8:
                dt = datetime.strptime(time_str[:8], "%Y%m%d").replace(tzinfo=KST)
            else:
                continue

            # 가격 파싱 (절대값 — 키움은 하락 시 음수로 반환)
            open_price = abs(int(item.get("open_pric") or item.get("시가") or item.get("stck_oprc") or 0))
            high_price = abs(int(item.get("high_pric") or item.get("고가") or item.get("stck_hgpr") or 0))
            low_price = abs(int(item.get("low_pric") or item.get("저가") or item.get("stck_lwpr") or 0))
            close_price = abs(int(item.get("cur_prc") or item.get("현재가") or item.get("stck_prpr") or 0))
            volume = abs(int(item.get("trde_qty") or item.get("거래량") or item.get("cntg_vol") or 0))

            if open_price == 0 and close_price == 0:
                continue

            candles.append({
                "time": int(dt.timestamp()),
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume,
            })
        except (ValueError, TypeError) as e:
            logger.warning(f"키움 캔들 파싱 오류: {e} — item={item}")
            continue

    # 연속조회 여부: 키움은 최대 900개 반환, 900개면 더 있을 가능성
    has_more_data = len(raw_list) >= 900
    # 연속조회 시 next 값은 응답에서 가져오거나, 기본 "2" 사용
    next_val = data.get("next", "2") if has_more_data else "0"

    # 시간순 정렬 (키움은 최신→과거 순으로 반환)
    candles.sort(key=lambda c: c["time"])

    logger.info(
        f"📊 키움 ka10080: stock={stock_code} tic={tic_scope} "
        f"next={next_key} raw={len(raw_list)} parsed={len(candles)} "
        f"has_more={has_more_data}"
    )

    return candles, has_more_data, next_val


# ==========================================
# 편의 함수
# ==========================================

def is_kiwoom_available() -> bool:
    """키움 API가 사용 가능한지 확인"""
    return _auth.is_available


# 프론트엔드 타임프레임 → 키움 틱범위 매핑
TIMEFRAME_TO_TIC_SCOPE = {
    "1m": "1",
    "3m": "3",
    "5m": "5",
    "10m": "10",
    "15m": "15",
    "30m": "30",
    "45m": "45",
    "1h": "60",
}
