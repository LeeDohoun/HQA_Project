from __future__ import annotations

import inspect
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from .dart import DartDisclosureCollector
from .kis_chart import KISChartCollector
from .naver_forum import NaverStockChartCollector, NaverStockForumCollector
from .naver_news import NaverNewsCollector
from .types import CollectRequest, DocumentRecord, MarketRecord


@dataclass
class IngestionRunReport:
    stock_code: str
    stock_name: str
    enabled_sources: List[str]
    source_success: Dict[str, bool] = field(default_factory=dict)
    # deprecated aliases are maintained for compatibility:
    # - collector_status ~= collector_ok
    # - source_data_present ~= data_present
    source_data_present: Dict[str, bool] = field(default_factory=dict)
    collector_status: Dict[str, bool] = field(default_factory=dict)
    collector_ok: Dict[str, bool] = field(default_factory=dict)
    data_present: Dict[str, bool] = field(default_factory=dict)
    source_counts: Dict[str, int] = field(default_factory=dict)
    document_counts: Dict[str, int] = field(default_factory=dict)
    market_data_counts: Dict[str, int] = field(default_factory=dict)
    raw_saved_counts: Dict[str, int] = field(default_factory=dict)
    failures: Dict[str, str] = field(default_factory=dict)
    warnings: Dict[str, str] = field(default_factory=dict)


@dataclass
class CollectResult:
    documents: List[DocumentRecord] = field(default_factory=list)
    market_records: List[MarketRecord] = field(default_factory=list)
    report: IngestionRunReport | None = None


class IngestionService:
    def collect_target_documents(self, request: CollectRequest) -> CollectResult:
        return self.collect(request)

    def collect(self, request: CollectRequest) -> CollectResult:
        report = IngestionRunReport(
            stock_code=request.target.stock_code,
            stock_name=request.target.stock_name,
            enabled_sources=list(request.enabled_sources),
        )

        docs: List[DocumentRecord] = []
        market_records: List[MarketRecord] = []

        if "news" in request.enabled_sources:
            self._safe_collect_news(request, docs, report)
        if "dart" in request.enabled_sources:
            self._safe_collect_dart(request, docs, report)
        if "forum" in request.enabled_sources:
            self._safe_collect_forum(request, docs, report)
        if "chart" in request.enabled_sources:
            self._safe_collect_chart(request, docs, market_records, report)
        if "quote" in request.enabled_sources:
            report.collector_status["quote"] = False
            report.collector_ok["quote"] = False
            report.source_data_present["quote"] = False
            report.data_present["quote"] = False
            report.source_success["quote"] = False
            report.market_data_counts["quote"] = 0
            report.source_counts["quote"] = 0
            report.warnings["quote"] = "enabled_but_not_implemented"

        for source in request.enabled_sources:
            collector_ok = bool((report.collector_ok or {}).get(source, False))
            data_present = bool((report.data_present or {}).get(source, False))
            report.source_success[source] = collector_ok and data_present

        return CollectResult(documents=docs, market_records=market_records, report=report)

    def collect_general_news(
        self,
        keywords: List[str],
        max_items: int,
        from_date: str,
        to_date: str,
        theme_key: str,
        raw_output_dir: str,
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
                    row.metadata["collected_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
                docs.extend(rows)
                self._save_raw_documents(rows, raw_output_dir, "news", theme_key)
            except Exception as e:
                print(f"[WARN][GENERAL NEWS] keyword='{keyword}' collect failed: {e}")
        return docs

    @staticmethod
    def _build_news_keyword(stock_name: str, stock_code: str) -> str:
        name = stock_name.strip()
        if len(name) <= 2:
            return f"{name} 주가"
        return f"{stock_name} {stock_code} 주식"

    def _safe_collect_news(self, request: CollectRequest, docs: List[DocumentRecord], report: IngestionRunReport) -> None:
        try:
            collector = NaverNewsCollector()
            sig = inspect.signature(collector.collect)
            kwargs = {"max_items": request.max_news}
            if "from_date" in sig.parameters:
                kwargs["from_date"] = request.from_date
            if "to_date" in sig.parameters:
                kwargs["to_date"] = request.to_date
            rows = collector.collect(self._build_news_keyword(request.target.stock_name, request.target.stock_code), **kwargs)
            rows = self._attach_stock_info(rows, request.target.stock_name, request.target.stock_code, request.theme_key)
            docs.extend(rows)
            report.collector_status["news"] = True
            report.collector_ok["news"] = True
            report.source_data_present["news"] = len(rows) > 0
            report.data_present["news"] = len(rows) > 0
            report.source_success["news"] = report.collector_ok["news"] and report.data_present["news"]
            report.source_counts["news"] = len(rows)
            report.document_counts["news"] = len(rows)
            report.raw_saved_counts["news"] = self._save_raw_documents(rows, request.raw_output_dir, "news", request.theme_key)
            if len(rows) == 0:
                report.warnings["news"] = "collector_ok_but_no_documents"
        except Exception as e:
            report.collector_status["news"] = False
            report.collector_ok["news"] = False
            report.source_success["news"] = False
            report.source_data_present["news"] = False
            report.data_present["news"] = False
            report.failures["news"] = str(e)
            print(f"[WARN][{request.target.stock_name}] news collect failed: {e}")

    def _safe_collect_dart(self, request: CollectRequest, docs: List[DocumentRecord], report: IngestionRunReport) -> None:
        if not request.target.corp_code:
            report.collector_status["dart"] = False
            report.collector_ok["dart"] = False
            report.source_success["dart"] = False
            report.source_data_present["dart"] = False
            report.data_present["dart"] = False
            report.document_counts["dart"] = 0
            report.failures["dart"] = "corp_code 없음"
            print(f"[WARN][DART] corp_code 없음: {request.target.stock_name}({request.target.stock_code})")
            return
        if not request.dart_api_key:
            report.collector_status["dart"] = False
            report.collector_ok["dart"] = False
            report.source_success["dart"] = False
            report.source_data_present["dart"] = False
            report.data_present["dart"] = False
            report.document_counts["dart"] = 0
            report.failures["dart"] = "DART_API_KEY 없음"
            print("[WARN][DART] DART_API_KEY 없음")
            return

        try:
            rows = DartDisclosureCollector(api_key=request.dart_api_key).collect(
                corp_code=request.target.corp_code,
                bgn_de=request.from_date,
                end_de=request.to_date,
            )
            rows = self._attach_stock_info(rows, request.target.stock_name, request.target.stock_code, request.theme_key)
            docs.extend(rows)
            report.collector_status["dart"] = True
            report.collector_ok["dart"] = True
            report.source_data_present["dart"] = len(rows) > 0
            report.data_present["dart"] = len(rows) > 0
            report.source_success["dart"] = report.collector_ok["dart"] and report.data_present["dart"]
            report.source_counts["dart"] = len(rows)
            report.document_counts["dart"] = len(rows)
            report.raw_saved_counts["dart"] = self._save_raw_documents(rows, request.raw_output_dir, "dart", request.theme_key)
            if len(rows) == 0:
                report.warnings["dart"] = "collector_ok_but_no_documents"
        except Exception as e:
            report.collector_status["dart"] = False
            report.collector_ok["dart"] = False
            report.source_success["dart"] = False
            report.source_data_present["dart"] = False
            report.data_present["dart"] = False
            report.failures["dart"] = str(e)
            print(f"[WARN][{request.target.stock_name}] dart collect failed: {e}")

    def _safe_collect_forum(self, request: CollectRequest, docs: List[DocumentRecord], report: IngestionRunReport) -> None:
        try:
            rows = NaverStockForumCollector().collect(
                stock_code=request.target.stock_code,
                pages=request.forum_pages,
                from_date=request.from_date,
                to_date=request.to_date,
            )
            rows = self._attach_stock_info(rows, request.target.stock_name, request.target.stock_code, request.theme_key)
            docs.extend(rows)
            report.collector_status["forum"] = True
            report.collector_ok["forum"] = True
            report.source_data_present["forum"] = len(rows) > 0
            report.data_present["forum"] = len(rows) > 0
            report.source_success["forum"] = report.collector_ok["forum"] and report.data_present["forum"]
            report.source_counts["forum"] = len(rows)
            report.document_counts["forum"] = len(rows)
            report.raw_saved_counts["forum"] = self._save_raw_documents(rows, request.raw_output_dir, "forum", request.theme_key)
            if len(rows) == 0:
                report.warnings["forum"] = "collector_ok_but_no_documents"
        except Exception as e:
            report.collector_status["forum"] = False
            report.collector_ok["forum"] = False
            report.source_success["forum"] = False
            report.source_data_present["forum"] = False
            report.data_present["forum"] = False
            report.failures["forum"] = str(e)
            print(f"[WARN][{request.target.stock_name}] forum collect failed: {e}")

    def _safe_collect_chart(
        self,
        request: CollectRequest,
        docs: List[DocumentRecord],
        market_records: List[MarketRecord],
        report: IngestionRunReport,
    ) -> None:
        try:
            rows = NaverStockChartCollector().collect(
                stock_code=request.target.stock_code,
                pages=request.chart_pages,
                from_date=request.from_date,
                to_date=request.to_date,
            )
            rows = self._attach_stock_info(rows, request.target.stock_name, request.target.stock_code, request.theme_key)
            docs.extend(rows)

            naver_market_rows = [
                MarketRecord(
                    source_type="chart",
                    stock_name=request.target.stock_name,
                    stock_code=request.target.stock_code,
                    timestamp=r.published_at or "",
                    open=r.metadata.get("open", ""),
                    high=r.metadata.get("high", ""),
                    low=r.metadata.get("low", ""),
                    close=r.metadata.get("close", ""),
                    volume=r.metadata.get("volume", ""),
                    metadata={"source": "naver"},
                )
                for r in rows
            ]

            current_market_rows = list(naver_market_rows)
            market_records.extend(naver_market_rows)

            if os.getenv("KIS_APP_KEY") and os.getenv("KIS_APP_SECRET"):
                try:
                    kis_rows = KISChartCollector().collect_daily(
                        stock_name=request.target.stock_name,
                        stock_code=request.target.stock_code,
                        from_date=request.from_date,
                        to_date=request.to_date,
                    )
                    current_market_rows.extend(kis_rows)
                    market_records.extend(kis_rows)
                except Exception as e:
                    print(f"[WARN][{request.target.stock_name}] KIS chart collect skipped: {e}")

            report.collector_status["chart"] = True
            report.collector_ok["chart"] = True
            report.source_data_present["chart"] = len(current_market_rows) > 0
            report.data_present["chart"] = len(current_market_rows) > 0
            report.source_success["chart"] = report.collector_ok["chart"] and report.data_present["chart"]
            report.source_counts["chart"] = len(current_market_rows)
            report.market_data_counts["chart"] = len(current_market_rows)
            report.raw_saved_counts["chart"] = self._save_raw_market_records(
                current_market_rows,
                request.raw_output_dir,
                request.theme_key,
            )
            if len(current_market_rows) == 0:
                report.warnings["chart"] = "collector_ok_but_no_market_records"
        except Exception as e:
            report.collector_status["chart"] = False
            report.collector_ok["chart"] = False
            report.source_success["chart"] = False
            report.source_data_present["chart"] = False
            report.data_present["chart"] = False
            report.failures["chart"] = str(e)
            print(f"[WARN][{request.target.stock_name}] chart collect failed: {e}")

    @staticmethod
    def _attach_stock_info(
        docs: List[DocumentRecord],
        stock_name: str,
        stock_code: str,
        theme_key: str,
    ) -> List[DocumentRecord]:
        collected_at = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

        for doc in docs:
            if not doc.stock_name:
                doc.stock_name = stock_name
            if not doc.stock_code:
                doc.stock_code = stock_code

            doc.metadata = doc.metadata or {}

            if "stock_name" not in doc.metadata:
                doc.metadata["stock_name"] = doc.stock_name or stock_name
            if "stock_code" not in doc.metadata:
                doc.metadata["stock_code"] = doc.stock_code or stock_code

            doc.metadata["theme_key"] = theme_key
            doc.metadata["collected_at"] = collected_at

        return docs

    def _save_raw_documents(self, docs: List[DocumentRecord], raw_output_dir: str, source: str, theme_key: str) -> int:
        if not docs:
            return 0
        raw_dir = Path(raw_output_dir) / source
        raw_dir.mkdir(parents=True, exist_ok=True)
        output_path = raw_dir / f"{theme_key}.jsonl"

        with output_path.open("a", encoding="utf-8") as f:
            for doc in docs:
                f.write(json.dumps(asdict(doc), ensure_ascii=False) + "\n")
        return len(docs)

    def _save_raw_market_records(self, rows: List[MarketRecord], raw_output_dir: str, theme_key: str) -> int:
        if not rows:
            return 0
        raw_dir = Path(raw_output_dir) / "chart"
        raw_dir.mkdir(parents=True, exist_ok=True)
        output_path = raw_dir / f"{theme_key}.jsonl"

        with output_path.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")
        return len(rows)
