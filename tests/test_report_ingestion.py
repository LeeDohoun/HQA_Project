from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ingestion import CollectRequest, DocumentRecord, IngestionService, StockTarget
from src.ingestion.naver_report import NaverReportCollector
from src.rag.raw_layer2_builder import RawLayer2Builder


def test_naver_report_collector_parses_company_list_item():
    pytest.importorskip("bs4")

    html = """
    <div class="box_type_m">
      <table>
        <tr>
          <td><a href="/item/main.naver?code=005930">삼성전자</a></td>
          <td>
            <a href="/research/company_read.naver?nid=1&page=1&searchType=itemCode&itemCode=005930">
              HBM 수혜와 실적 회복
            </a>
          </td>
          <td>테스트증권</td>
          <td><a href="https://ssl.pstatic.net/imgstock/upload/research/company/sample.pdf">PDF</a></td>
          <td class="date">26.04.15</td>
        </tr>
      </table>
    </div>
    """

    collector = NaverReportCollector()
    items = collector._parse_company_list_items(
        html,
        page=1,
        fallback_stock_name="삼성전자",
        fallback_stock_code="005930",
    )

    assert len(items) == 1
    assert items[0].title == "HBM 수혜와 실적 회복"
    assert items[0].stock_code == "005930"
    assert items[0].broker == "테스트증권"
    assert items[0].published_at == "2026-04-15T00:00:00"
    assert items[0].pdf_url.endswith("sample.pdf")


def test_ingestion_service_collects_report_source(monkeypatch, tmp_path):
    class FakeReportCollector:
        def collect_by_stock(
            self,
            stock_name,
            stock_code,
            max_items,
            from_date,
            to_date,
            max_pages,
        ):
            assert stock_name == "삼성전자"
            assert stock_code == "005930"
            assert max_items == 2
            assert from_date == "20260101"
            assert to_date == "20260417"
            assert max_pages == 3
            return [
                DocumentRecord(
                    source_type="report",
                    title="삼성전자 실적 회복",
                    content="삼성전자 반도체 실적 회복 전망 " * 40,
                    url="https://example.com/report.pdf",
                    stock_name=stock_name,
                    stock_code=stock_code,
                    published_at="2026-04-15T00:00:00",
                    metadata={
                        "broker": "테스트증권",
                        "pdf_url": "https://example.com/report.pdf",
                        "content_extraction": "pymupdf",
                    },
                )
            ]

    monkeypatch.setattr("src.ingestion.services.NaverReportCollector", FakeReportCollector)

    service = IngestionService()
    request = CollectRequest(
        target=StockTarget(stock_name="삼성전자", stock_code="005930"),
        max_news=0,
        forum_pages=0,
        chart_pages=0,
        from_date="20260101",
        to_date="20260417",
        dart_api_key="",
        theme_key="반도체",
        enabled_sources=["report"],
        max_reports=2,
        report_days_back=0,
        report_pages=3,
        raw_output_dir=str(tmp_path / "raw"),
    )
    result = service.collect_target_documents(request)
    second_result = service.collect_target_documents(request)

    assert len(result.documents) == 1
    assert result.report is not None
    assert result.report.source_success["report"] is True
    assert result.report.raw_saved_counts["report"] == 1
    assert second_result.report is not None
    assert second_result.report.raw_saved_counts["report"] == 0

    raw_path = tmp_path / "raw" / "report" / "반도체.jsonl"
    saved = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()]
    assert len(saved) == 1
    assert saved[0]["source_type"] == "report"
    assert saved[0]["metadata"]["theme_key"] == "반도체"
    assert saved[0]["metadata"]["pdf_url"] == "https://example.com/report.pdf"


def test_raw_layer2_builder_validates_report_docs(tmp_path):
    raw_dir = tmp_path / "raw" / "report"
    raw_dir.mkdir(parents=True)

    valid_content = "삼성전자 HBM 수요와 실적 회복 전망을 분석합니다. " * 60
    rows = [
        {
            "source_type": "report",
            "title": "삼성전자 HBM 수혜",
            "content": valid_content,
            "url": "https://example.com/valid.pdf",
            "stock_name": "삼성전자",
            "stock_code": "005930",
            "published_at": "2026-04-15T00:00:00",
            "metadata": {
                "theme_key": "반도체",
                "broker": "테스트증권",
                "pdf_url": "https://example.com/valid.pdf",
                "content_extraction": "pymupdf",
            },
        },
        {
            "source_type": "report",
            "title": "짧은 리포트",
            "content": "본문 부족",
            "url": "https://example.com/short.pdf",
            "stock_name": "삼성전자",
            "stock_code": "005930",
            "published_at": "2026-04-15T00:00:00",
            "metadata": {
                "theme_key": "반도체",
                "broker": "테스트증권",
                "pdf_url": "https://example.com/short.pdf",
            },
        },
    ]
    raw_path = raw_dir / "반도체.jsonl"
    raw_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    result = RawLayer2Builder(data_dir=str(tmp_path)).rebuild_theme(
        theme_key="반도체",
        update_mode="overwrite",
    )

    assert result["raw_docs_count"] == 2
    assert result["skipped_invalid_count_by_source"]["report"] == 1
    assert result["document_source_counts"]["report"] >= 1

    corpus_path = Path(result["canonical_stats"]["bm25_path"]).parent / "corpus.jsonl"
    corpus_rows = [json.loads(line) for line in corpus_path.read_text(encoding="utf-8").splitlines()]
    assert {row["metadata"]["source_type"] for row in corpus_rows} == {"report"}
