from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from src.config.settings import get_settings, reset_settings_cache
from src.agents.analyst import AnalystAgent
from src.evidence.retriever import EvidenceRetriever
from src.retrieval.vector_store import SimpleVectorStore


def test_settings_resolve_hqa_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HQA_DATA_DIR", str(tmp_path / "custom-data"))
    reset_settings_cache()

    settings = get_settings()
    assert settings.data_dir == (tmp_path / "custom-data").resolve()
    assert "env" in settings.env_status.message.lower() or "기본값" in settings.env_status.message

    reset_settings_cache()


def test_canonical_retriever_falls_back_to_pipeline_vector_store():
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        vector_dir = data_dir / "vector_stores"
        vector_dir.mkdir(parents=True, exist_ok=True)

        store = SimpleVectorStore()
        store.add_texts(
            ["삼성전자 실적 전망이 개선되고 있습니다."],
            [{"source_type": "report", "title": "삼성전자 전망", "doc_id": "doc-1"}],
        )
        store.save(str(vector_dir / "report_vector_store.json"))

        retriever = EvidenceRetriever(data_dir=tmpdir)
        results = retriever.search("삼성전자 실적 전망", top_k=3)

        assert results, "canonical index가 없어도 pipeline vector store로 폴백해야 합니다."
        assert results[0]["source_type"] == "report"
        assert "삼성전자" in results[0]["text"]


def test_search_for_context_explains_missing_indexes():
    with tempfile.TemporaryDirectory() as tmpdir:
        raw_dir = Path(tmpdir) / "raw" / "news"
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "반도체.jsonl").write_text(
            json.dumps(
                {
                    "source_type": "news",
                    "title": "샘플 기사",
                    "content": "샘플 내용",
                    "stock_code": "005930",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

        retriever = EvidenceRetriever(data_dir=tmpdir)
        context = retriever.search_for_context("삼성전자")

        assert "retrieval 인덱스가 없습니다" in context
        assert "build_evidence_index.py" in context


def test_run_agent_demo_fails_fast_without_retrieval_assets(tmp_path):
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "scripts" / "run_agent_demo.py"),
        "--query",
        "삼성전자 실적 전망 요약",
        "--data-dir",
        str(tmp_path / "missing-data"),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])

    assert result.returncode == 1
    assert "retrieval 자산이 없습니다" in result.stdout


def test_run_agent_demo_succeeds_with_mock_provider(tmp_path):
    index_dir = tmp_path / "canonical_index" / "sample"
    index_dir.mkdir(parents=True, exist_ok=True)

    record = {
        "text": "삼성전자는 메모리 업황 회복의 수혜가 기대된다.",
        "metadata": {
            "source_type": "report",
            "title": "삼성전자 메모리 전망",
            "stock_name": "삼성전자",
            "stock_code": "005930",
            "published_at": "2026-04-01",
            "doc_id": "sample-1",
        },
    }
    (index_dir / "corpus.jsonl").write_text(
        json.dumps(record, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    store = SimpleVectorStore()
    store.add_texts([record["text"]], [record["metadata"]])
    store.save(str(index_dir / "combined_vector_store.json"))

    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "scripts" / "run_agent_demo.py"),
        "--query",
        "삼성전자 메모리 전망 요약",
        "--data-dir",
        str(tmp_path),
    ]
    env = dict(os.environ)
    env["LLM_PROVIDER"] = "mock"
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
    )

    assert result.returncode == 0
    assert '"status": "ok"' in result.stdout
    assert "삼성전자 메모리 전망" in result.stdout
