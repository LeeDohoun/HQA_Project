# 파일: backend/api/routes/stocks.py
"""
종목 검색 / 실시간 시세 엔드포인트
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.dependencies import verify_api_key
from backend.api.schemas import (
    RealtimePriceResponse,
    StockInfo,
    StockSearchResponse,
    StockSearchResult,
)

router = APIRouter(prefix="/stocks", tags=["Stocks"], dependencies=[Depends(verify_api_key)])


@router.get("/search", response_model=StockSearchResponse)
async def search_stocks(
    q: str = Query(..., min_length=1, max_length=100, description="종목명 또는 종목코드"),
):
    """
    종목 검색
    
    종목명(한글) 또는 종목코드(6자리)로 검색합니다.
    """
    try:
        from src.utils.stock_mapper import get_mapper

        mapper = get_mapper()

        # 종목코드로 검색
        if q.isdigit() and len(q) == 6:
            name = mapper.get_name(q)
            if name:
                return StockSearchResponse(
                    results=[StockSearchResult(name=name, code=q)],
                    total=1,
                )
            return StockSearchResponse(results=[], total=0)

        # 종목명으로 검색
        results = mapper.search(q)
        items = [StockSearchResult(name=r["name"], code=r["code"]) for r in results[:10]]
        return StockSearchResponse(results=items, total=len(items))

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"종목 검색 실패: {str(e)}")


@router.get("/{stock_code}/price", response_model=RealtimePriceResponse)
async def get_realtime_price(stock_code: str):
    """
    실시간 시세 조회
    
    한국투자증권 API를 통해 실시간 시세를 조회합니다.
    """
    if not stock_code.isdigit() or len(stock_code) != 6:
        raise HTTPException(status_code=400, detail="종목코드는 6자리 숫자여야 합니다.")

    try:
        from src.tools.realtime_tool import KISRealtimeTool
        from src.utils.stock_mapper import get_mapper

        mapper = get_mapper()
        stock_name = mapper.get_name(stock_code) or stock_code

        tool = KISRealtimeTool()
        if not tool.is_available:
            raise HTTPException(
                status_code=503,
                detail="실시간 시세 API가 설정되지 않았습니다. KIS_APP_KEY/KIS_APP_SECRET을 확인하세요.",
            )

        price = tool.get_current_price(stock_code)
        if not price:
            raise HTTPException(status_code=404, detail=f"시세 정보를 가져올 수 없습니다: {stock_code}")

        return RealtimePriceResponse(
            stock=StockInfo(name=stock_name, code=stock_code),
            current_price=price.current_price,
            change=price.change,
            change_rate=price.change_rate,
            open_price=price.open_price,
            high_price=price.high_price,
            low_price=price.low_price,
            volume=price.volume,
            market_cap=getattr(price, "market_cap", None),
            per=getattr(price, "per", None),
            pbr=getattr(price, "pbr", None),
            timestamp=getattr(price, "timestamp", datetime.now()),
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"시세 조회 실패: {str(e)}")
