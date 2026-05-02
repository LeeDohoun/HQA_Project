from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_temporal_rag_filters_future_documents(tmp_path):
    from backtesting import TemporalRAG

    _write_jsonl(
        tmp_path / "canonical_index" / "ai" / "corpus.jsonl",
        [
            {
                "text": "old HBM news",
                "metadata": {
                    "source_type": "news",
                    "stock_code": "005930",
                    "stock_name": "삼성전자",
                    "title": "old",
                    "published_at": "2025-01-10T00:00:00",
                },
            },
            {
                "text": "future HBM news",
                "metadata": {
                    "source_type": "news",
                    "stock_code": "005930",
                    "stock_name": "삼성전자",
                    "title": "future",
                    "published_at": "2025-03-10T00:00:00",
                },
            },
        ],
    )

    rag = TemporalRAG(data_dir=str(tmp_path), theme_key="ai")
    rows = rag.filter_records("20250201", source_types=["news"])
    assert len(rows) == 1
    assert rows[0]["metadata"]["title"] == "old"


def test_temporal_rag_applies_forum_lookback(tmp_path):
    from backtesting import TemporalRAG

    _write_jsonl(
        tmp_path / "canonical_index" / "ai" / "corpus.jsonl",
        [
            {
                "text": "old forum",
                "metadata": {
                    "source_type": "forum",
                    "stock_code": "005930",
                    "published_at": "2025-01-01T00:00:00",
                },
            },
            {
                "text": "recent forum",
                "metadata": {
                    "source_type": "forum",
                    "stock_code": "005930",
                    "published_at": "2025-03-25T00:00:00",
                },
            },
        ],
    )

    rag = TemporalRAG(data_dir=str(tmp_path), theme_key="ai")
    rows = rag.filter_records("20250401", source_types=["forum"], lookback_days={"forum": 30})
    assert len(rows) == 1
    assert rows[0]["text"] == "recent forum"


def test_temporal_price_loader_filters_future_and_bad_ohlc(tmp_path):
    pd = pytest.importorskip("pandas")
    from backtesting import TemporalPriceLoader

    _write_jsonl(
        tmp_path / "market_data" / "ai" / "chart.jsonl",
        [
            {
                "source_type": "chart",
                "stock_code": "005930",
                "timestamp": "2025-01-01T00:00:00",
                "open": "100",
                "high": "120",
                "low": "90",
                "close": "110",
                "volume": "1000",
            },
            {
                "source_type": "chart",
                "stock_code": "005930",
                "timestamp": "2025-01-02T00:00:00",
                "open": "0",
                "high": "0",
                "low": "0",
                "close": "110",
                "volume": "0",
            },
            {
                "source_type": "chart",
                "stock_code": "005930",
                "timestamp": "2025-02-01T00:00:00",
                "open": "200",
                "high": "220",
                "low": "190",
                "close": "210",
                "volume": "2000",
            },
        ],
    )

    df = TemporalPriceLoader(data_dir=str(tmp_path), theme_key="ai").get_stock_data(
        "005930",
        as_of_date="20250131",
        days=10,
    )
    assert len(df) == 1
    assert df.iloc[0]["Close"] == 110


def test_build_period_snapshot_writes_filtered_sources(tmp_path):
    from backtesting.temporal_rag import build_period_snapshot

    _write_jsonl(
        tmp_path / "canonical_index" / "ai" / "corpus.jsonl",
        [
            {
                "text": "2025 news",
                "metadata": {
                    "source_type": "news",
                    "stock_code": "005930",
                    "title": "in period",
                    "published_at": "2025-06-01T00:00:00",
                },
            },
            {
                "text": "2026 news",
                "metadata": {
                    "source_type": "news",
                    "stock_code": "005930",
                    "title": "out period",
                    "published_at": "2026-01-01T00:00:00",
                },
            },
            {
                "text": "2025 forum",
                "metadata": {
                    "source_type": "forum",
                    "stock_code": "005930",
                    "title": "excluded source",
                    "published_at": "2025-06-02T00:00:00",
                },
            },
        ],
    )

    result = build_period_snapshot(
        data_dir=str(tmp_path),
        theme_key="ai",
        from_date="20250101",
        to_date="20251231",
        output_name="ai_2025_news",
        source_types=["news"],
    )

    out_dir = Path(result["output_dir"])
    assert result["combined_count"] == 1
    assert result["source_counts"] == {"news": 1}
    assert (out_dir / "combined.jsonl").exists()
    assert (out_dir / "news.jsonl").exists()
    assert not (out_dir / "forum.jsonl").exists()


def test_clean_period_rag_keeps_dart_chunks_and_reduces_noisy_duplicates():
    from backtesting.clean_period_rag import clean_rows

    rows = [
        {
            "text": "공시 본문 첫 번째 청크",
            "metadata": {
                "source_type": "dart",
                "stock_code": "005930",
                "title": "사업보고서",
                "published_at": "2026-03-01T00:00:00",
                "chunk_index": 0,
            },
        },
        {
            "text": "공시 본문 두 번째 청크",
            "metadata": {
                "source_type": "dart",
                "stock_code": "005930",
                "title": "사업보고서",
                "published_at": "2026-03-01T00:00:00",
                "chunk_index": 1,
            },
        },
        {
            "text": "중복 종토방 글입니다. 같은 내용이 반복됩니다.",
            "metadata": {
                "source_type": "forum",
                "stock_code": "005930",
                "title": "반복글",
                "published_at": "2026-04-01T00:00:00",
            },
        },
        {
            "text": "중복 종토방 글입니다. 같은 내용이 반복됩니다.",
            "metadata": {
                "source_type": "forum",
                "stock_code": "005930",
                "title": "반복글",
                "published_at": "2026-04-02T00:00:00",
            },
        },
        {
            "text": "https://example.com 봐라",
            "metadata": {
                "source_type": "forum",
                "stock_code": "005930",
                "title": "짧은 링크",
                "published_at": "2026-04-02T00:00:00",
            },
        },
        {
            "text": "뉴스 첫 번째 청크 내용입니다.",
            "metadata": {
                "source_type": "news",
                "stock_code": "005930",
                "title": "같은 뉴스",
                "published_at": "2026-04-03T00:00:00",
                "chunk_index": 0,
            },
        },
        {
            "text": "뉴스 두 번째 청크 내용입니다.",
            "metadata": {
                "source_type": "news",
                "stock_code": "005930",
                "title": "같은 뉴스",
                "published_at": "2026-04-03T00:00:00",
                "chunk_index": 1,
            },
        },
        {
            "text": "뉴스 세 번째 청크 내용입니다.",
            "metadata": {
                "source_type": "news",
                "stock_code": "005930",
                "title": "같은 뉴스",
                "published_at": "2026-04-03T00:00:00",
                "chunk_index": 2,
            },
        },
    ]

    cleaned, report = clean_rows(
        rows,
        forum_min_normalized_chars=10,
        max_news_chunks_per_title=2,
        max_forum_chunks_per_title=1,
    )

    by_source = report["output_source_counts"]
    assert by_source["dart"] == 2
    assert by_source["forum"] == 1
    assert by_source["news"] == 2
    assert report["drop_reasons"]["forum_exact_text_duplicate"] == 1
    assert report["drop_reasons"]["forum_short_text"] == 1
    assert report["drop_reasons"]["news_same_title_chunk_cap"] == 1


def test_leader_backtest_writes_backend_ready_payload(tmp_path):
    pd = pytest.importorskip("pandas")
    from backtesting.leader_backtest import run_leader_backtest

    targets = [
        {"stock_name": "Alpha", "stock_code": "000001"},
        {"stock_name": "Beta", "stock_code": "000002"},
        {"stock_name": "Gamma", "stock_code": "000003"},
    ]
    _write_jsonl(tmp_path / "raw" / "theme_targets" / "ai.jsonl", targets)

    dates = pd.bdate_range("2025-01-01", periods=80)
    chart_rows = []
    for i, day in enumerate(dates):
        for name, code, slope in [
            ("Alpha", "000001", 2.0),
            ("Beta", "000002", 0.4),
            ("Gamma", "000003", -0.2),
        ]:
            close = 100 + i * slope
            chart_rows.append(
                {
                    "source_type": "chart",
                    "stock_name": name,
                    "stock_code": code,
                    "timestamp": day.strftime("%Y-%m-%dT00:00:00"),
                    "open": close - 1,
                    "high": close + 2,
                    "low": close - 2,
                    "close": close,
                    "volume": 1000 + i,
                }
            )
    _write_jsonl(tmp_path / "market_data" / "ai" / "chart.jsonl", chart_rows)

    _write_jsonl(
        tmp_path / "canonical_index" / "ai" / "corpus.jsonl",
        [
            {
                "text": "Alpha AI catalyst",
                "metadata": {
                    "source_type": "news",
                    "stock_name": "Alpha",
                    "stock_code": "000001",
                    "published_at": "2025-02-10T00:00:00",
                },
            }
        ],
    )

    result = run_leader_backtest(
        data_dir=tmp_path,
        theme="AI",
        theme_key="ai",
        from_date="20250228",
        to_date="20250331",
        top_n=1,
        hold_days=5,
        min_history_days=20,
        output_dir=tmp_path / "results",
        task_id="bt-test",
    )

    assert result["mode"] == "backtest"
    assert result["result_type"] == "backtest"
    assert result["metrics"]["rebalance_count"] >= 1
    assert result["leaders"][0]["stock_code"] == "000001"
    assert Path(result["artifacts"]["result_json"]).exists()

    risk_result = run_leader_backtest(
        data_dir=tmp_path,
        theme="AI",
        theme_key="ai",
        from_date="20250228",
        to_date="20250331",
        top_n=3,
        hold_days=5,
        min_history_days=20,
        max_return_20d=0.05,
        output_dir=tmp_path / "results",
        task_id="bt-test-risk",
    )

    assert risk_result["metrics"]["risk_off_count"] >= 1
    assert risk_result["risk"]["reject_counts"]["overheated_20d"] >= 1


def test_leader_backtest_uses_point_in_time_theme_membership(tmp_path):
    pd = pytest.importorskip("pandas")
    from backtesting.leader_backtest import run_leader_backtest

    _write_jsonl(
        tmp_path / "raw" / "theme_targets" / "ai.jsonl",
        [
            {"stock_name": "Early", "stock_code": "000001"},
            {"stock_name": "Future", "stock_code": "000002"},
        ],
    )
    _write_jsonl(
        tmp_path / "raw" / "theme_membership" / "ai.jsonl",
        [
            {
                "theme_key": "ai",
                "stock_name": "Early",
                "stock_code": "000001",
                "first_seen_at": "2025-01-01",
                "last_seen_at": "",
                "source": "test",
                "membership_confidence": 1.0,
            },
            {
                "theme_key": "ai",
                "stock_name": "Future",
                "stock_code": "000002",
                "first_seen_at": "2025-04-30",
                "last_seen_at": "",
                "source": "test",
                "membership_confidence": 1.0,
            },
        ],
    )

    dates = pd.bdate_range("2025-01-01", periods=80)
    chart_rows = []
    for i, day in enumerate(dates):
        for name, code, slope in [
            ("Early", "000001", 0.5),
            ("Future", "000002", 3.0),
        ]:
            close = 100 + i * slope
            chart_rows.append(
                {
                    "source_type": "chart",
                    "stock_name": name,
                    "stock_code": code,
                    "timestamp": day.strftime("%Y-%m-%dT00:00:00"),
                    "open": close - 1,
                    "high": close + 2,
                    "low": close - 2,
                    "close": close,
                    "volume": 1000 + i,
                }
            )
    _write_jsonl(tmp_path / "market_data" / "ai" / "chart.jsonl", chart_rows)
    _write_jsonl(tmp_path / "canonical_index" / "ai" / "corpus.jsonl", [])

    result = run_leader_backtest(
        data_dir=tmp_path,
        theme="AI",
        theme_key="ai",
        from_date="20250228",
        to_date="20250331",
        top_n=1,
        hold_days=5,
        min_history_days=20,
        output_dir=tmp_path / "results",
        task_id="bt-test-membership",
    )

    assert result["metadata"]["point_in_time_universe"] is True
    assert result["metadata"]["membership_count"] == 2
    assert {row["stock_code"] for row in result["positions"]} == {"000001"}
