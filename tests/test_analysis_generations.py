from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json

import pytest

from src.evidence.index_builder import EvidenceIndexBuilder
from src.ingestion.storage import atomic_write, read_rows, write_rows
from src.ingestion.types import DocumentRecord
from src.runner.analysis_data import LocalAnalysisData


def fixture_document(now, version="A", *, legacy=False):
    return asdict(DocumentRecord(source_type="news", title="삼성전자 계약",
        content=f"삼성전자는 계약 {version}를 발표했다. 상세한 계약 정보와 금액은 공시 원문에 기록되어 있다.",
        url="https://news.example/1", stock_code="005930", stock_name="삼성전자",
        published_at=(now - timedelta(days=1)).isoformat(),
        metadata={"collected_at": (now - timedelta(minutes=60 if version == "A" else 30)).isoformat(),
                  **({} if legacy else {"version_id": version})}))


def write_inputs(root, now, versions=("A",), *, legacy=False):
    write_rows(root / "raw/news/theme.jsonl", [fixture_document(now, version, legacy=legacy) for version in versions])
    write_rows(root / "raw/chart/theme.jsonl", [{"source_type": "chart", "stock_code": "005930",
        "timestamp": (now - timedelta(days=1)).isoformat(), "open": "100", "high": "110", "low": "90",
        "close": "100" if len(versions) == 1 else "101", "volume": "1000",
        "metadata": {"collected_at": (now - timedelta(minutes=20)).isoformat()}}])
    write_rows(root / "raw/theme_targets/theme.jsonl", [{"stock_code": "005930", "stock_name": "삼성전자"}])


def pointer(root):
    return json.loads((root / "canonical_index/theme/current.json").read_text(encoding="utf-8"))


def loader_with_stub_prices(root, monkeypatch):
    def features(rows, as_of):
        return {"current_price": float(rows[-1]["close"])}, rows

    monkeypatch.setattr("src.runner.analysis_data.price_features", features)
    loader = LocalAnalysisData(data_dir=str(root))
    monkeypatch.setattr(loader, "_filter_errors", lambda _: [])
    return loader


def test_legacy_body_revisions_survive_chunk_dedup_without_copying_full_body(tmp_path):
    now = datetime.now(timezone.utc)
    write_inputs(tmp_path, now, ("A", "B"), legacy=True)
    result = EvidenceIndexBuilder(str(tmp_path)).rebuild_theme("theme")
    corpus = read_rows(tmp_path / "canonical_index/theme/corpus.jsonl")
    assert result["raw_docs_count"] == len(corpus) == 2
    assert "계약 A" in corpus[0]["text"] and "계약 B" in corpus[1]["text"]
    assert all("content" not in row["metadata"] for row in corpus)


def test_analysis_pins_price_and_documents_to_same_generation_across_rebuild(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    write_inputs(tmp_path, now)
    builder = EvidenceIndexBuilder(str(tmp_path))
    builder.rebuild_theme("theme")
    first_pointer = pointer(tmp_path)
    loader = loader_with_stub_prices(tmp_path, monkeypatch)
    candidates, errors = loader.load_universe(now)
    assert not errors
    candidate = candidates[0]
    assert candidate["features"]["current_price"] == 100
    assert candidate["theme_generations"] == {"theme": first_pointer["generation"]}
    write_inputs(tmp_path, now, ("A", "B"))
    builder.rebuild_theme("theme")
    assert pointer(tmp_path)["generation"] != first_pointer["generation"]
    pinned = loader.load_evidence(candidate, now)
    assert "계약 A" in pinned["documents"][0]["text"]
    fresh, _ = loader.load_universe(now)
    assert fresh[0]["features"]["current_price"] == 101
    assert "계약 B" in loader.load_evidence(fresh[0], now)["documents"][0]["text"]


@pytest.mark.parametrize("failure_at", ["documents", "pointer"])
def test_failed_build_keeps_published_generation_unchanged(tmp_path, monkeypatch, failure_at):
    now = datetime.now(timezone.utc)
    write_inputs(tmp_path, now)
    builder = EvidenceIndexBuilder(str(tmp_path))
    builder.rebuild_theme("theme")
    before = pointer(tmp_path)
    write_inputs(tmp_path, now, ("A", "B"))

    def fail_write(path, rows):
        if path.name == "documents.jsonl":
            raise OSError("fixture document publication failure")
        return write_rows(path, rows)

    def fail_pointer(path, text):
        if path.name == "current.json":
            raise OSError("fixture pointer publication failure")
        return atomic_write(path, text)

    with monkeypatch.context() as scoped:
        scoped.setattr("src.evidence.index_builder." + ("write_rows" if failure_at == "documents" else "atomic_write"),
                       fail_write if failure_at == "documents" else fail_pointer)
        with pytest.raises(OSError, match="publication failure"):
            builder.rebuild_theme("theme")
    assert pointer(tmp_path) == before
    loader = loader_with_stub_prices(tmp_path, monkeypatch)
    candidates, _ = loader.load_universe(now)
    assert candidates[0]["features"]["current_price"] == 100
    assert "계약 A" in loader.load_evidence(candidates[0], now)["documents"][0]["text"]
    assert builder.rebuild_theme("theme")["reused"] is False
    assert pointer(tmp_path)["generation"] != before["generation"]


def test_failed_initial_managed_build_cannot_fall_back_to_partial_legacy_corpus(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    write_inputs(tmp_path, now)

    def fail_write(path, rows):
        if path.name == "documents.jsonl":
            raise OSError("fixture failure")
        return write_rows(path, rows)

    monkeypatch.setattr("src.evidence.index_builder.write_rows", fail_write)
    with pytest.raises(OSError):
        EvidenceIndexBuilder(str(tmp_path)).rebuild_theme("theme")
    assert (tmp_path / "canonical_index/theme/corpus.jsonl").exists()
    with pytest.raises(ValueError, match="unpublished_analysis_generation"):
        LocalAnalysisData(data_dir=str(tmp_path)).load_evidence({"stock_code": "005930", "theme_keys": ["theme"]}, now)


def test_missing_pinned_generation_fails_without_switching_to_new_current(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    write_inputs(tmp_path, now)
    builder = EvidenceIndexBuilder(str(tmp_path))
    builder.rebuild_theme("theme")
    loader = loader_with_stub_prices(tmp_path, monkeypatch)
    candidates, _ = loader.load_universe(now)
    pinned = candidates[0]["theme_generations"]["theme"]
    write_inputs(tmp_path, now, ("A", "B"))
    builder.rebuild_theme("theme")
    (tmp_path / "canonical_index/theme/generations" / pinned / "documents.jsonl").unlink()
    with pytest.raises(ValueError, match="missing_analysis_generation"):
        loader.load_evidence(candidates[0], now)


def test_unchanged_inputs_reuse_existing_generation(tmp_path):
    now = datetime.now(timezone.utc)
    write_inputs(tmp_path, now)
    builder = EvidenceIndexBuilder(str(tmp_path))
    builder.rebuild_theme("theme")
    before = pointer(tmp_path)
    assert builder.rebuild_theme("theme")["reused"] is True
    assert pointer(tmp_path) == before


def test_undated_legacy_prices_are_quarantined_without_republishing_old_chart(tmp_path):
    now = datetime.now(timezone.utc)
    write_inputs(tmp_path, now)
    builder = EvidenceIndexBuilder(str(tmp_path))
    builder.rebuild_theme("theme")
    original = read_rows(tmp_path / "raw/chart/theme.jsonl")
    original[0]["metadata"] = {}
    write_rows(tmp_path / "raw/chart/theme.jsonl", original)
    builder.rebuild_theme("theme")
    assert read_rows(tmp_path / "raw/chart/theme.jsonl") == original
    quarantined = read_rows(tmp_path / "quarantine/chart/theme.jsonl")
    assert quarantined[0]["reason"] == "missing_or_invalid_price_observation_time"
    current = pointer(tmp_path)["generation"]
    assert read_rows(tmp_path / "canonical_index/theme/generations" / current / "chart.jsonl") == []
