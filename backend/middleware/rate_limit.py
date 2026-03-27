# 파일: backend/middleware/rate_limit.py
"""
Rate Limiting 미들웨어

IP 기반 요청 제한으로 API 남용을 방지합니다.
Redis 사용 시 분산 Rate Limiting, 없으면 인메모리 방식.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Dict, List

from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from backend.config import get_settings


class RateLimitMiddleware(BaseHTTPMiddleware):
    """IP 기반 Rate Limiting"""

    def __init__(self, app, requests_per_minute: int = 30):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self._requests: Dict[str, List[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        # 헬스체크 엔드포인트는 제외
        if request.url.path in ("/health", "/health/detailed", "/docs", "/openapi.json"):
            return await call_next(request)

        client_ip = self._get_client_ip(request)
        now = time.time()
        window = 60  # 1분

        # 오래된 요청 제거
        self._requests[client_ip] = [
            t for t in self._requests[client_ip] if now - t < window
        ]

        if len(self._requests[client_ip]) >= self.requests_per_minute:
            raise HTTPException(
                status_code=429,
                detail=f"요청 제한 초과. 분당 {self.requests_per_minute}회까지 허용됩니다.",
                headers={"Retry-After": "60"},
            )

        self._requests[client_ip].append(now)

        # 남은 횟수 헤더 추가
        response = await call_next(request)
        remaining = self.requests_per_minute - len(self._requests[client_ip])
        response.headers["X-RateLimit-Limit"] = str(self.requests_per_minute)
        response.headers["X-RateLimit-Remaining"] = str(max(0, remaining))
        return response

    def _get_client_ip(self, request: Request) -> str:
        """클라이언트 IP 추출 (프록시 뒤에서도 동작)"""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"
