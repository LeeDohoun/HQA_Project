# 파일: backend/middleware/error_handler.py
"""
전역 에러 핸들링

모든 예외를 일관된 JSON 형식으로 반환합니다.
"""

import logging
import traceback

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """전역 에러 핸들링 미들웨어"""

    async def dispatch(self, request: Request, call_next):
        try:
            response = await call_next(request)
            return response
        except Exception as e:
            logger.exception(f"처리되지 않은 예외: {request.method} {request.url.path}")

            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "detail": "내부 서버 오류가 발생했습니다.",
                    "error_type": type(e).__name__,
                    "path": str(request.url.path),
                },
            )
