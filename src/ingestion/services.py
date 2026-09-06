from __future__ import annotations

# File role:
# - Orchestrate per-source collectors for one stock target.
# - Persist raw outputs, attach metadata, and return a structured run report.

import inspect
import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

from .dart import DartDisclosureCollector
from .dart_financials import DartFinancialStatementCollector
from .krx_chart import KrxChartCollector
from .naver_forum import NaverStockForumCollector
from .naver_news import NaverNewsCollector
from .types import CollectRequest, DocumentRecord, FinancialSnapshot, MarketRecord
from .storage import read_rows, save_episodes, write_rows, file_lock


@dataclass
class IngestionRunReport:
    stock_code: str
    stock_name: str
    enabled_sources: List[str]
    source_success: Dict[str, bool] = field(default_factory=dict)
    source_counts: Dict[str, int] = field(default_factory=dict)
    raw_saved_counts: Dict[str, int] = field(default_factory=dict)
    skipped_counts: Dict[str, int] = field(default_factory=dict)
    failures: Dict[str, str] = field(default_factory=dict)
    source_status: Dict[str, str] = field(default_factory=dict)
    cache_hits: Dict[str, bool] = field(default_factory=dict)
    rejected_counts: Dict[str, int] = field(default_factory=dict)


@dataclass
class CollectResult:
    documents: List[DocumentRecord] = field(default_factory=list)
    market_records: List[MarketRecord] = field(default_factory=list)
    financial_snapshots: List[FinancialSnapshot] = field(default_factory=list)
    report: IngestionRunReport | None = None


class IngestionService:
    def __init__(
        self,
        krx_chart_collector: KrxChartCollector | None = None,
        financial_collector: DartFinancialStatementCollector | None = None,
    ):
        self.krx_chart_collector = krx_chart_collector or KrxChartCollector()
        self.financial_collector = financial_collector

    # Public entry used by the CLI when collecting one stock target.
    def collect_target_documents(self, request: CollectRequest) -> CollectResult:
        return self.collect(request)

    # Source collectors are isolated so one failure does not stop the others.
    def collect(self, request: CollectRequest) -> CollectResult:
        self._validate_request_dates(request)
        if request.incremental:
            return self._collect_shared(request)
        report = IngestionRunReport(
            stock_code=request.target.stock_code,
            stock_name=request.target.stock_name,
            enabled_sources=list(request.enabled_sources),
        )

        docs: List[DocumentRecord] = []
        market_records: List[MarketRecord] = []
        financial_snapshots: List[FinancialSnapshot] = []

        if "news" in request.enabled_sources:
            self._safe_collect_news(request, docs, report)
        if "dart" in request.enabled_sources:
            self._safe_collect_dart(request, docs, report)
        if "financials" in request.enabled_sources:
            self._safe_collect_financials(request, financial_snapshots, report)
        if "forum" in request.enabled_sources:
            self._safe_collect_forum(request, docs, report)
        if "chart" in request.enabled_sources:
            self._safe_collect_chart(request, docs, market_records, report)

        for source in request.enabled_sources:
            report.source_status[source] = (
                "error" if not report.source_success.get(source, False)
                else "success" if report.source_counts.get(source, 0) else "no_data"
            )
        return CollectResult(
            documents=docs,
            market_records=market_records,
            financial_snapshots=financial_snapshots,
            report=report,
        )

    def _collect_shared(self, request: CollectRequest) -> CollectResult:
        from .collection_state import collect_shared
        report = IngestionRunReport(request.target.stock_code, request.target.stock_name,
                                    list(request.enabled_sources))
        result = CollectResult(report=report)
        for source in dict.fromkeys(request.enabled_sources):
            try:
                payload, cached = collect_shared(request, source, self.collect)
                documents = [DocumentRecord(**row) for row in payload["documents"]]
                market = [MarketRecord(**row) for row in payload["market_records"]]
                financials = [FinancialSnapshot(**row) for row in payload["financial_snapshots"]]
                for row in [*documents, *market, *financials]:
                    row.metadata["theme_key"] = request.theme_key
                source_report = payload["report"]
                report.cache_hits[source] = cached
                if source_report["source_success"].get(source):
                    count = self._project_shared_archive(request, source,
                        require_records=source_report["source_counts"].get(source, 0) > 0)
                    source_report["raw_saved_counts"][source] = count
                    source_report["skipped_counts"][source] = max(0, source_report["source_counts"][source] - count)
                result.documents.extend(documents)
                result.market_records.extend(market)
                result.financial_snapshots.extend(financials)
                for field_name in ("source_success", "source_counts", "raw_saved_counts", "skipped_counts", "failures", "source_status", "rejected_counts"):
                    getattr(report, field_name).update(source_report.get(field_name, {}))
            except Exception as exc:
                report.source_success[source] = False
                report.source_status[source] = "error"
                report.failures[source] = str(exc)
        return result

    def _project_shared_archive(self, request: CollectRequest, source: str, *, require_records: bool = False) -> int:
        archive = Path(request.raw_output_dir) / source / f"_shared_{request.target.stock_code}.jsonl"
        destination = Path(request.raw_output_dir) / source / f"{request.theme_key}.jsonl"
        with file_lock(archive.with_suffix(".jsonl.lock")):
            shared = read_rows(archive)
            if require_records and not shared:
                raise ValueError(f"missing_shared_archive:{source}:{request.target.stock_code}")
        with file_lock(destination.with_suffix(".jsonl.lock")):
            existing = read_rows(destination)
            merged = {}
            for row in existing + shared:
                row = {**row, "metadata": {**(row.get("metadata") or {}), "theme_key": request.theme_key}}
                meta = row["metadata"]
                key = meta.get("version_id") or hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
                if key in merged and merged[key] != row:
                    raise ValueError(f"conflicting_shared_observation:{source}:{request.target.stock_code}")
                merged.setdefault(key, row)
            def observed_at(row):
                value = (row.get("metadata") or {}).get("collected_at")
                if not value:
                    return datetime.min.replace(tzinfo=timezone.utc)
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return (parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
            ordered = sorted(merged.values(), key=observed_at)
            write_rows(destination, ordered)
            if source == "financials":
                market = Path(request.raw_output_dir).parent / "market_data" / request.theme_key / "financials.jsonl"
                write_rows(market, ordered)
            return len(merged) - len(existing)

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
                    row.metadata["collected_at"] = self._utc_timestamp()
                docs.extend(rows)
                self._save_raw_documents(rows, raw_output_dir, "news", theme_key)
            except Exception as e:
                raise RuntimeError(f"general news collection failed:{type(e).__name__}") from None
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
            from .naver_news import match_news_entity
            accepted, rejected = [], []
            for row in rows:
                match = match_news_entity(row, request.target.stock_code, request.target.stock_name)
                row.metadata = {**(row.metadata or {}), "entity_match": match}
                if match["matched"]:
                    accepted.append(row)
                else:
                    row.metadata.update(requested_stock_code=request.target.stock_code,
                                        quarantine_reason="unverified_news_subject", collected_at=self._utc_timestamp())
                    rejected.append(asdict(row))
            if rejected:
                path = Path(request.raw_output_dir) / "quarantine" / "news" / f"{request.theme_key}.jsonl"
                save_episodes(path, rejected, lambda row: row["url"],
                              lambda row: self._document_revision_key(row, "news")[2])
            report.rejected_counts["news"] = len(rejected)
            rows = accepted
            rows = self._attach_stock_info(rows, request.target.stock_name, request.target.stock_code, request.theme_key)
            docs.extend(rows)
            report.source_success["news"] = True
            report.source_counts["news"] = len(rows)
            report.raw_saved_counts["news"] = self._save_raw_documents(rows, request.raw_output_dir, "news", request.theme_key)
            report.skipped_counts["news"] = len(rows) - report.raw_saved_counts["news"]
        except Exception as e:
            report.source_success["news"] = False
            report.failures["news"] = str(e)
            print(f"[WARN][{request.target.stock_name}] news collect failed: {e}")

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
            report.skipped_counts["dart"] = len(rows) - report.raw_saved_counts["dart"]
        except Exception as e:
            report.source_success["dart"] = False
            report.failures["dart"] = str(e)
            print(f"[WARN][{request.target.stock_name}] dart collect failed: {e}")

    def _safe_collect_financials(
        self,
        request: CollectRequest,
        snapshots: List[FinancialSnapshot],
        report: IngestionRunReport,
    ) -> None:
        if not request.target.corp_code:
            report.source_success["financials"] = False
            report.failures["financials"] = "corp_code 없음"
            print(f"[WARN][DART FINANCIALS] corp_code 없음: {request.target.stock_name}({request.target.stock_code})")
            return
        if not request.dart_api_key:
            report.source_success["financials"] = False
            report.failures["financials"] = "DART_API_KEY 없음"
            print("[WARN][DART FINANCIALS] DART_API_KEY 없음")
            return

        try:
            collector = self.financial_collector or DartFinancialStatementCollector(api_key=request.dart_api_key)
            if hasattr(collector, "collect_annual_series"):
                collected_snapshots = collector.collect_annual_series(
                    stock_name=request.target.stock_name,
                    stock_code=request.target.stock_code,
                    corp_code=request.target.corp_code,
                    from_date=request.from_date,
                    to_date=request.to_date,
                    years=3,
                )
            else:
                snapshot = collector.collect_latest_annual(
                    stock_name=request.target.stock_name,
                    stock_code=request.target.stock_code,
                    corp_code=request.target.corp_code,
                    from_date=request.from_date,
                    to_date=request.to_date,
                )
                collected_snapshots = [snapshot] if snapshot is not None else []
            if not collected_snapshots:
                report.source_success["financials"] = False
                report.failures["financials"] = "재무제표 없음"
                return
            collected_at = datetime.now(timezone.utc).isoformat()
            for snapshot in collected_snapshots:
                snapshot.metadata = snapshot.metadata or {}
                snapshot.metadata["theme_key"] = request.theme_key
                snapshot.metadata["collected_at"] = collected_at
            snapshots.extend(collected_snapshots)
            report.source_success["financials"] = True
            report.source_counts["financials"] = len(collected_snapshots)
            report.raw_saved_counts["financials"] = self._save_raw_financial_snapshots(
                collected_snapshots,
                request.raw_output_dir,
                request.theme_key,
            )
            report.skipped_counts["financials"] = len(collected_snapshots) - report.raw_saved_counts["financials"]
            self._save_market_financial_snapshots(
                collected_snapshots,
                request.raw_output_dir,
                request.theme_key,
            )
        except Exception as e:
            report.source_success["financials"] = False
            report.failures["financials"] = str(e)
            print(f"[WARN][{request.target.stock_name}] financials collect failed: {e}")

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
            report.skipped_counts["forum"] = len(rows) - report.raw_saved_counts["forum"]
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
            rows = self.krx_chart_collector.collect_daily(
                stock_name=request.target.stock_name,
                stock_code=request.target.stock_code,
                from_date=request.from_date,
                to_date=request.to_date,
            )
            for row in rows:
                row.metadata = row.metadata or {}
                row.metadata.setdefault("source", "krx")
                row.metadata["theme_key"] = request.theme_key
                row.metadata["collected_at"] = self._utc_timestamp()
            market_records.extend(rows)
            report.source_success["chart"] = True
            report.source_counts["chart"] = len(rows)
            report.raw_saved_counts["chart"] = self._save_raw_market_records(
                rows,
                request.raw_output_dir,
                request.theme_key,
            )
            report.skipped_counts["chart"] = len(rows) - report.raw_saved_counts["chart"]
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
        collected_at = IngestionService._utc_timestamp()

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

        return save_episodes(output_path, [asdict(doc) for doc in docs],
            lambda row: self._document_revision_key(row, source)[:2],
            lambda row: self._document_revision_key(row, source)[2])

    def _save_raw_market_records(self, rows: List[MarketRecord], raw_output_dir: str, theme_key: str) -> int:
        # Market rows are stored separately from text documents.
        if not rows:
            return 0
        raw_dir = Path(raw_output_dir) / "chart"
        raw_dir.mkdir(parents=True, exist_ok=True)
        output_path = raw_dir / f"{theme_key}.jsonl"

        return save_episodes(output_path, [asdict(row) for row in rows],
                             self._market_record_key_from_row, self._numeric_revision)

    def _save_raw_financial_snapshots(
        self,
        rows: List[FinancialSnapshot],
        raw_output_dir: str,
        theme_key: str,
    ) -> int:
        if not rows:
            return 0
        raw_dir = Path(raw_output_dir) / "financials"
        raw_dir.mkdir(parents=True, exist_ok=True)
        output_path = raw_dir / f"{theme_key}.jsonl"

        return save_episodes(output_path, [asdict(row) for row in rows],
                             self._financial_snapshot_key, self._numeric_revision)

    def _save_market_financial_snapshots(
        self,
        rows: List[FinancialSnapshot],
        raw_output_dir: str,
        theme_key: str,
    ) -> int:
        if not rows:
            return 0
        data_dir = Path(raw_output_dir).parent
        market_dir = data_dir / "market_data" / theme_key
        market_dir.mkdir(parents=True, exist_ok=True)
        output_path = market_dir / "financials.jsonl"

        raw_path = Path(raw_output_dir) / "financials" / f"{theme_key}.jsonl"
        with file_lock(raw_path.with_suffix(".jsonl.lock")):
            return write_rows(output_path, read_rows(raw_path))

    @staticmethod
    def _financial_snapshot_key(row: Dict) -> tuple[str, str, str, str]:
        return (
            str(row.get("stock_code", "")).strip(),
            str(row.get("fiscal_year", "")).strip(),
            str(row.get("report_code", "")).strip(),
            str((row.get("metadata") or {}).get("fs_div") or ""),
        )

    @staticmethod
    def _numeric_revision(row: Dict) -> str:
        metadata = {key: value for key, value in (row.get("metadata") or {}).items()
                    if key not in {"collected_at", "available_at", "observed_at", "version_id", "theme_key"}}
        payload = {key: value for key, value in row.items() if key not in {"metadata", "stock_name"}}
        return hashlib.sha256(json.dumps({**payload, "metadata": metadata}, sort_keys=True,
            ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def _document_revision_key(row: Dict, source: str) -> tuple[str, str, str]:
        metadata = row.get("metadata") or {}
        event_fields = (
            "rcept_no", "rcept_dt", "structured_endpoint", "structured_rcept_no", "structured_row",
            "remark", "is_correction", "has_correction", "is_withdrawal", "has_body",
            "published_at_precision", "published_at_source", "supersedes_source_ids",
            "evidence_scope", "body_source", "body_extracted",
            "original_rcept_no", "supersedes_rcept_no",
            "publication_time_status", "entity_match",
        )
        revision = {
            "title": row.get("title") or "",
            "content": row.get("content") or "",
            "published_at": row.get("published_at") or "",
            "event_metadata": {key: metadata[key] for key in event_fields if key in metadata},
        }
        digest = hashlib.sha256(json.dumps(revision, ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":")).encode("utf-8")).hexdigest()
        return (
            IngestionService._document_dedupe_key_from_row(row, source),
            str(row.get("stock_code") or metadata.get("stock_code") or "").strip(),
            digest,
        )

    @staticmethod
    def _document_dedupe_key(doc: DocumentRecord, source: str) -> str:
        metadata = doc.metadata or {}
        return IngestionService._document_dedupe_key_from_values(
            source=source,
            url=doc.url,
            stock_code=doc.stock_code or metadata.get("stock_code", ""),
            title=doc.title,
            published_at=doc.published_at or "",
            rcept_no=metadata.get("rcept_no", ""),
        )

    @staticmethod
    def _document_dedupe_key_from_row(row: Dict, source: str) -> str:
        metadata = row.get("metadata") or {}
        return IngestionService._document_dedupe_key_from_values(
            source=source,
            url=row.get("url", ""),
            stock_code=row.get("stock_code") or metadata.get("stock_code", ""),
            title=row.get("title", ""),
            published_at=row.get("published_at", ""),
            rcept_no=metadata.get("rcept_no", ""),
        )

    @staticmethod
    def _document_dedupe_key_from_values(
        *,
        source: str,
        url: object = "",
        stock_code: object = "",
        title: object = "",
        published_at: object = "",
        rcept_no: object = "",
    ) -> str:
        normalized_source = str(source or "").strip()
        normalized_url = str(url or "").strip()
        normalized_stock_code = str(stock_code or "").strip()
        normalized_title = str(title or "").strip()
        normalized_published_at = str(published_at or "").strip()
        normalized_rcept_no = str(rcept_no or "").strip()

        if normalized_source in {"news", "general_news"} and normalized_url:
            return f"{normalized_source}|{normalized_url}"
        if normalized_source == "dart" and normalized_rcept_no:
            return f"{normalized_source}|{normalized_rcept_no}"
        if normalized_source == "forum":
            if normalized_url:
                return f"{normalized_source}|{normalized_url}"
            return f"{normalized_source}|{normalized_stock_code}|{normalized_title}|{normalized_published_at}"
        if normalized_url:
            return f"{normalized_source}|{normalized_url}"
        return f"{normalized_source}|{normalized_stock_code}|{normalized_title}|{normalized_published_at}"

    @staticmethod
    def _market_record_key(row: MarketRecord) -> str:
        return IngestionService._market_record_key_from_values(
            source=row.source_type,
            stock_code=row.stock_code,
            timestamp=row.timestamp,
            frequency=(row.metadata or {}).get("frequency", "daily"),
        )

    @staticmethod
    def _market_record_key_from_row(row: Dict) -> str:
        metadata = row.get("metadata") or {}
        return IngestionService._market_record_key_from_values(
            source=row.get("source_type", ""),
            stock_code=row.get("stock_code", ""),
            timestamp=row.get("timestamp", ""),
            frequency=metadata.get("frequency", "daily"),
        )

    @staticmethod
    def _market_record_key_from_values(
        *,
        source: object,
        stock_code: object,
        timestamp: object,
        frequency: object,
    ) -> str:
        return "|".join(
            [
                str(source or "").strip(),
                str(stock_code or "").strip(),
                str(timestamp or "").strip(),
                str(frequency or "daily").strip(),
            ]
        )

    @staticmethod
    def _validate_request_dates(request: CollectRequest) -> None:
        from_dt = IngestionService._parse_yyyymmdd(request.from_date, "from_date")
        to_dt = IngestionService._parse_yyyymmdd(request.to_date, "to_date")
        if from_dt > to_dt:
            raise ValueError("from_date must be earlier than or equal to to_date")

    @staticmethod
    def _parse_yyyymmdd(value: str, field_name: str) -> datetime:
        text = str(value or "").strip()
        if len(text) != 8 or not text.isdigit():
            raise ValueError(f"{field_name} must use YYYYMMDD format")
        try:
            return datetime.strptime(text, "%Y%m%d")
        except ValueError as exc:
            raise ValueError(f"{field_name} must be a valid YYYYMMDD date") from exc

    @staticmethod
    def _utc_timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()
