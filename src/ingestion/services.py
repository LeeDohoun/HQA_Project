from __future__ import annotations

import inspect
from typing import List

from .dart import DartDisclosureCollector
from .naver_forum import NaverStockChartCollector, NaverStockForumCollector
from .naver_news import NaverNewsCollector
from .types import CollectRequest, DocumentRecord


def _build_news_keyword(stock_name: str, stock_code: str) -> str:
    name = stock_name.strip()
    if len(name) <= 2:
        return f"{name} 주가"
    return f"{stock_name} {stock_code} 주식"


class IngestionService:
    def collect_target_documents(self, request: CollectRequest) -> List[DocumentRecord]:
        target = request.target
        docs: List[DocumentRecord] = []

        news: List[DocumentRecord] = []
        dart: List[DocumentRecord] = []
        forum: List[DocumentRecord] = []
        chart: List[DocumentRecord] = []

        try:
            collector = NaverNewsCollector()
            sig = inspect.signature(collector.collect)
            kwargs = {"max_items": request.max_news}
            if "from_date" in sig.parameters:
                kwargs["from_date"] = request.from_date
            if "to_date" in sig.parameters:
                kwargs["to_date"] = request.to_date
            news = collector.collect(_build_news_keyword(target.stock_name, target.stock_code), **kwargs)
            news = self._attach_stock_info(news, target.stock_name, target.stock_code, request.theme_key)
        except Exception as e:
            print(f"[WARN][{target.stock_name}] news collect failed: {e}")

        if not target.corp_code:
            print(f"[WARN][DART] corp_code 없음: {target.stock_name}({target.stock_code})")
        elif not request.dart_api_key:
            print("[WARN][DART] DART_API_KEY 없음")
        else:
            try:
                dart = DartDisclosureCollector(api_key=request.dart_api_key).collect(
                    corp_code=target.corp_code,
                    bgn_de=request.from_date,
                    end_de=request.to_date,
                )
                dart = self._attach_stock_info(dart, target.stock_name, target.stock_code, request.theme_key)
            except Exception as e:
                print(f"[WARN][{target.stock_name}] dart collect failed: {e}")

        try:
            forum = NaverStockForumCollector().collect(
                stock_code=target.stock_code,
                pages=request.forum_pages,
                from_date=request.from_date,
                to_date=request.to_date,
            )
            forum = self._attach_stock_info(forum, target.stock_name, target.stock_code, request.theme_key)
        except Exception as e:
            print(f"[WARN][{target.stock_name}] forum collect failed: {e}")

        try:
            chart = NaverStockChartCollector().collect(
                stock_code=target.stock_code,
                pages=request.chart_pages,
                from_date=request.from_date,
                to_date=request.to_date,
            )
            chart = self._attach_stock_info(chart, target.stock_name, target.stock_code, request.theme_key)
        except Exception as e:
            print(f"[WARN][{target.stock_name}] chart collect failed: {e}")

        docs.extend(news)
        docs.extend(dart)
        docs.extend(forum)
        docs.extend(chart)
        return docs

    def collect_general_news(
        self,
        keywords: List[str],
        max_items: int,
        from_date: str,
        to_date: str,
        theme_key: str,
    ) -> List[DocumentRecord]:
        if not keywords:
            return []

        collector = NaverNewsCollector()
        docs: List[DocumentRecord] = []
        for keyword in keywords:
            try:
                rows = collector.collect(
                    keyword=keyword,
                    max_items=max_items,
                    from_date=from_date,
                    to_date=to_date,
                )
                for row in rows:
                    row.source_type = "general_news"
                    row.metadata = row.metadata or {}
                    row.metadata["general_keyword"] = keyword
                    row.metadata["theme_key"] = theme_key
                docs.extend(rows)
            except Exception as e:
                print(f"[WARN][GENERAL NEWS] keyword='{keyword}' collect failed: {e}")

        return docs

    @staticmethod
    def _attach_stock_info(
        docs: List[DocumentRecord],
        stock_name: str,
        stock_code: str,
        theme_key: str,
    ) -> List[DocumentRecord]:
        for doc in docs:
            if not doc.stock_name:
                doc.stock_name = stock_name
            if not doc.stock_code:
                doc.stock_code = stock_code

            doc.metadata = doc.metadata or {}
            doc.metadata["stock_name"] = stock_name
            doc.metadata["stock_code"] = stock_code
            doc.metadata["theme_key"] = theme_key
            doc.metadata["url"] = doc.url or ""
            doc.metadata["title"] = doc.title or ""
            doc.metadata["published_at"] = doc.published_at or ""
            doc.metadata["source_type"] = doc.source_type or ""

        return docs
