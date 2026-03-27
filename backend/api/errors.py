# 파일: backend/api/errors.py
"""
표준 에러 핸들링

모든 API에서 동일한 에러 응답 포맷을 사용합니다.
프론트엔드는 error_code 필드를 기반으로 사용자 메시지를 결정합니다.

사용 예:
    from backend.api.errors import api_error
    raise api_error(ErrorCode.CHART_LOAD_FAILED, "차트 데이터를 불러올 수 없습니다.", detail=str(e))
"""

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from backend.api.schemas import ErrorCode


def api_error(
    error_code: ErrorCode,
    message: str,
    status_code: int = 500,
    detail: str | None = None,
) -> HTTPException:
    """
    표준 에러 HTTPException 생성

    Args:
        error_code: ErrorCode enum 값
        message: 사용자에게 보여줄 메시지
        status_code: HTTP 상태 코드
        detail: 디버깅용 상세 정보 (선택)

    Returns:
        HTTPException (프론트엔드가 파싱할 수 있는 표준 포맷)
    """
    content = {
        "success": False,
        "error_code": error_code.value,
        "message": message,
    }
    if detail:
        content["detail"] = detail

    return HTTPException(status_code=status_code, detail=content)


# HTTP 상태코드별 기본 매핑
ERROR_STATUS_MAP = {
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.STOCK_INVALID_CODE: 400,
    ErrorCode.ANALYSIS_NOT_FOUND: 404,
    ErrorCode.STOCK_NOT_FOUND: 404,
    ErrorCode.CHART_NO_DATA: 404,
    ErrorCode.SERVICE_UNAVAILABLE: 503,
    ErrorCode.CHART_API_NOT_CONFIGURED: 503,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.CHART_LOAD_FAILED: 500,
    ErrorCode.ANALYSIS_FAILED: 500,
}


def raise_api_error(
    error_code: ErrorCode,
    message: str,
    detail: str | None = None,
) -> None:
    """
    편의 함수: error_code에 따라 자동으로 HTTP 상태코드를 결정하여 raise

    사용 예:
        raise_api_error(ErrorCode.CHART_LOAD_FAILED, "차트 로딩 실패", detail=str(e))
    """
    status_code = ERROR_STATUS_MAP.get(error_code, 500)
    raise api_error(error_code, message, status_code=status_code, detail=detail)
