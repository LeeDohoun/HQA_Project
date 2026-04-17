from __future__ import annotations

# File role:
# - Orchestrate per-source collectors for one stock target.
# - Persist raw outputs, attach metadata, and return a structured run report.

import inspect
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

from .dart import DartDisclosureCollector
from .kis_chart import KISChartCollector
from .naver_forum import NaverStockChartCollector, NaverStockForumCollector
from .naver_news import NaverNewsCollector
from .naver_report import NaverReportCollector
from .types import CollectRequest, DocumentRecord, MarketRecord


@dataclass
class IngestionRunReport:
    stock_code: str
    stock_name: str
    enabled_sources: List[str]
    source_success: Dict[str, bool] = field(default_factory=dict)
    source_counts: Dict[str, int] = field(default_factory=dict)
    raw_saved_counts: Dict[str, int] = field(default_factory=dict)
    failures: Dict[str, str] = field(default_factory=dict)


@dataclass
class CollectResult:
    documents: List[DocumentRecord] = field(default_factory=list)
    market_records: List[MarketRecord] = field(default_factory=list)
    report: IngestionRunReport | None = None


class IngestionService:
    def __init__(self, kis_chart_collector: KISChartCollector | None = None):
        self.kis_chart_collector = kis_chart_collector or KISChartCollector()

    # Public entry used by the CLI when collecting one stock target.
    def collect_target_documents(self, request: CollectRequest) -> CollectResult:
        return self.collect(request)

    # Source collectors are isolated so one failure does not stop the others.
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
        if "report" in request.enabled_sources:
            self._safe_collect_report(request, docs, report)
        if "dart" in request.enabled_sources:
            self._safe_collect_dart(request, docs, report)
        if "forum" in request.enabled_sources:
            self._safe_collect_forum(request, docs, report)
        if "chart" in request.enabled_sources:
            self._safe_collect_chart(request, docs, market_records, report)

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
            report.source_success["news"] = True
            report.source_counts["news"] = len(rows)
            report.raw_saved_counts["news"] = self._save_raw_documents(rows, request.raw_output_dir, "news", request.theme_key)
        except Exception as e:
            report.source_success["news"] = False
            report.failures["news"] = str(e)
            print(f"[WARN][{request.target.stock_name}] news collect failed: {e}")

    def _safe_collect_report(self, request: CollectRequest, docs: List[DocumentRecord], report: IngestionRunReport) -> None:
        if request.report_source.strip().lower() != "naver":
            report.source_success["report"] = False
            report.failures["report"] = f"unsupported report_source={request.report_source}"
            print(f"[WARN][REPORT] unsupported report_source={request.report_source}")
            return

        try:
            collector = NaverReportCollector()
            rows = collector.collect_by_stock(
                stock_name=request.target.stock_name,
                stock_code=request.target.stock_code,
                max_items=request.max_reports,
                from_date=self._resolve_report_from_date(request),
                to_date=request.to_date,
                max_pages=request.report_pages,
            )
            rows = self._attach_stock_info(rows, request.target.stock_name, request.target.stock_code, request.theme_key)
            docs.extend(rows)
            report.source_success["report"] = True
            report.source_counts["report"] = len(rows)
            report.raw_saved_counts["report"] = self._save_raw_documents(
                rows,
                request.raw_output_dir,
                "report",
                request.theme_key,
                dedupe_existing=True,
            )
        except Exception as e:
            report.source_success["report"] = False
            report.failures["report"] = str(e)
            print(f"[WARN][{request.target.stock_name}] report collect failed: {e}")

    def _safe_collect_dart(self, request: CollectRequest, docs: List[DocumentRecord], report: IngestionRunReport) -> None:
        if not request.target.corp_code:
            report.source_success["dart"] = False
            report.failures["dart"] = "corp_code 없음"
            print(f"[WARN][DART] corp_code 없음: {request.target.stock_name}({request.target.stock_code})")
            return
        if not request.dart_api_key:
            report.source_success["dart"] = False
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
            report.source_success["dart"] = True
            report.source_counts["dart"] = len(rows)
            report.raw_saved_counts["dart"] = self._save_raw_documents(rows, request.raw_output_dir, "dart", request.theme_key)
        except Exception as e:
            report.source_success["dart"] = False
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
            report.source_success["forum"] = True
            report.source_counts["forum"] = len(rows)
            report.raw_saved_counts["forum"] = self._save_raw_documents(rows, request.raw_output_dir, "forum", request.theme_key)
        except Exception as e:
            report.source_success["forum"] = False
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
                    kis_rows = self.kis_chart_collector.collect_daily(
                        stock_name=request.target.stock_name,
                        stock_code=request.target.stock_code,
                        from_date=request.from_date,
                        to_date=request.to_date,
                    )
                    current_market_rows.extend(kis_rows)
                    market_records.extend(kis_rows)
                except Exception as e:
                    print(f"[WARN][{request.target.stock_name}] KIS chart collect skipped: {e}")

            report.source_success["chart"] = True
            report.source_counts["chart"] = len(rows)
            report.raw_saved_counts["chart"] = self._save_raw_market_records(
                current_market_rows,
                request.raw_output_dir,
                request.theme_key,
            )
        except Exception as e:
            report.source_success["chart"] = False
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

    @staticmethod
    def _resolve_report_from_date(request: CollectRequest) -> str:
        candidates: List[str] = []
        if request.from_date:
            candidates.append(request.from_date)
        if request.report_days_back > 0:
            candidates.append((datetime.utcnow() - timedelta(days=request.report_days_back)).strftime("%Y%m%d"))
        compact_candidates = []
        for value in candidates:
            raw = str(value).strip()
            if not raw:
                continue
            digits = "".join(ch for ch in raw if ch.isdigit())
            if len(digits) >= 8:
                compact_candidates.append(digits[:8])
            elif len(digits) == 6:
                compact_candidates.append(f"20{digits}")
        return max(compact_candidates) if compact_candidates else ""

    def _save_raw_documents(
        self,
        docs: List[DocumentRecord],
        raw_output_dir: str,
        source: str,
        theme_key: str,
        dedupe_existing: bool = False,
    ) -> int:
        if not docs:
            return 0
        raw_dir = Path(raw_output_dir) / source
        raw_dir.mkdir(parents=True, exist_ok=True)
        output_path = raw_dir / f"{theme_key}.jsonl"

        seen_keys = set()
        if dedupe_existing and output_path.exists():
            with output_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        seen_keys.add(self._raw_document_key(json.loads(line)))
                    except json.JSONDecodeError:
                        continue

        saved = 0
        with output_path.open("a", encoding="utf-8") as f:
            for doc in docs:
                payload = asdict(doc)
                key = self._raw_document_key(payload)
                if dedupe_existing and key in seen_keys:
                    continue
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
                seen_keys.add(key)
                saved += 1
        return saved

    @staticmethod
    def _raw_document_key(row: Dict) -> str:
        metadata = row.get("metadata") or {}
        source_type = str(row.get("source_type") or metadata.get("source_type") or "").strip().lower()
        url = str(metadata.get("pdf_url") or row.get("url") or metadata.get("url") or "").strip()
        if url:
            return f"{source_type}|url|{url}"
        doc_id = str(row.get("doc_id") or metadata.get("doc_id") or "").strip()
        if doc_id:
            return f"{source_type}|doc_id|{doc_id}"
        title = str(row.get("title") or metadata.get("title") or "").strip()
        published_at = str(row.get("published_at") or metadata.get("published_at") or "").strip()
        stock_code = str(row.get("stock_code") or metadata.get("stock_code") or "").strip()
        return f"{source_type}|fallback|{stock_code}|{title}|{published_at}"

    def _save_raw_market_records(self, rows: List[MarketRecord], raw_output_dir: str, theme_key: str) -> int:
        # Market rows are stored separately from text documents.
        if not rows:
            return 0
        raw_dir = Path(raw_output_dir) / "chart"
        raw_dir.mkdir(parents=True, exist_ok=True)
        output_path = raw_dir / f"{theme_key}.jsonl"

        with output_path.open("a", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")
        return len(rows)
