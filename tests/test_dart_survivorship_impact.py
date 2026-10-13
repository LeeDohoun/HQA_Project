import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.research import dart_list_survivorship_impact as diagnostic
from src.ingestion.dart_listing import directory
from src.ingestion.storage import write_rows


def fixture(data):
    path = data / "reference/corp_codes.csv"
    path.parent.mkdir()
    path.write_text("corp_code,stock_code\n00000001,000010\n00000002,000020\n00000003,000030\n")
    write_rows(data / "reference/dart_company/companies.jsonl", [
        {"corp_code": "00000001", "corp_cls": "E"}, {"corp_code": "00000002", "corp_cls": "Y"},
        {"corp_code": "00000003", "corp_cls": "K"}])
    for day in ("20240102", "20240103"):
        write_rows(data / "market/krx_daily/2024" / f"{day}.jsonl",
                   [{"stock_code": c, "trade_date": day} for c in ("000010", "000020", "000030", "999990")])
    rows = [listing("20200515", "분기보고서 (2020.03)"),
            listing("20220515", "[기재정정]분기보고서 (2020.03)")]
    for row in rows:
        day = row["rcept_dt"]
        write_rows(directory(data) / "list" / day[:4] / f"{day}.jsonl", [row])
    saved = [{"corp_code": "00000001", "bsns_year": 2020, "rcept_no": r["rcept_no"], "fs_div": "CFS"} for r in rows]
    saved += [{"corp_code": "00000001", "bsns_year": 2020, "rcept_no": "20200814000001", "fs_div": "OFS"}]
    write_rows(data / "fundamentals/dart_quarterly/2020_11013.jsonl", saved)


def listing(day, title):
    return {"corp_code": "00000001", "stock_code": "000010", "corp_cls": "E", "corp_name": "과거상장",
            "rcept_dt": day, "rcept_no": day + "000001", "report_nm": title, "rm": "", "flr_nm": "회사"}


def snapshot(data):
    root = data / "research/lh002"
    root.mkdir(parents=True)
    stocks = [{"stock_code": code, "adv_20": 100 - i, "industry": "A" if i != 1 else "B",
               **{"disclosures_" + c: 0 if i != 1 else 1 for c in diagnostic.inputs.CATEGORIES}}
              for i, code in enumerate(("000010", "000020", "000030"))]
    bundle = {"decision_date": "2024-01-31", "stocks": stocks, "a_codes": [s["stock_code"] for s in stocks],
              "industries": [{"id": "GA", "industry": "A"}, {"id": "GB", "industry": "B"}]}
    mapping = diagnostic.inputs.id_map(bundle, True)
    call_b, call_c = "a" * 64, "b" * 64
    for key, ranked, avoid in [(call_b, ["000010", "000030"], []), (call_c, ["000020", "000030"], ["000010"])]:
        path = root / "calls" / f"{key}.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"key": key, "stage": "screening", "valid": True,
            "output": {"ranked": [{"id": mapping[c]} for c in ranked], "avoid": [{"id": mapping[c]} for c in avoid]}}))
    data_obj = {"observations": [{"number": n, "braces": '[{}]"\\'} for n in range(200)],
                "prepared": [{"bundle": bundle, "texts": {}}], "exclusions": []}
    (root / "screening_snapshot.json").write_text(json.dumps({"data": data_obj, "sha256": "cached-data-hash", "metadata": {"stage": "screening"}}, ensure_ascii=False, indent=2))
    decision = {"decision_date": bundle["decision_date"], "arms": {
        "b_stage1": {"selected": ["GA"]},
        "b_num": {"calls": [call_b], "candidates": ["000010", "000030"], "selected": ["000010"], "avoid": []},
        "c_num": {"calls": [call_c], "candidates": ["000020", "000030"], "selected": ["000030"], "avoid": ["000010"]}}}
    (root / "screening.json").write_text(json.dumps({"decisions": [decision]}))
    return root


def test_diagnostic_before_supplement_measures_krx_corrections_and_no_cache_without_writes(tmp_path):
    fixture(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = diagnostic.diagnose(data_dir=tmp_path)
    year = result["krx_by_year"]["2024"]
    assert year["affected_corps"] == 1 and year["affected_stock_days"] == 2 and year["krx_stock_days"] == 8
    assert year["affected_share"] == 0.25
    assert result["unmapped_stock_days"] == {"999990": 2}
    assert not result["supplement"]["available"] and result["supplement"]["by_year"] is None
    saved = result["quarterly"]["by_year"]["2020"]
    assert saved["versions"] == 3 and saved["corrections"] == saved["originals"] == saved["unlinked"] == 1
    assert saved["correction_delay_days"]["median"] == 730
    assert not result["lh002"]["available"]
    assert result["performance"]["peak_rss_mib"] < 6 * 1024
    assert "최초 수치는 복원하지 않았습니다" in diagnostic.korean_summary(result)
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_supplement_categories_and_exact_original_raise_rules(tmp_path):
    rows = [listing("20240102", name) for name in ("유상증자결정", "[기재정정]유상증자결정", "유무상증자결정", "전환사채권발행결정")]
    write_rows(directory(tmp_path) / "list_supplement/2024/20240102.jsonl", rows)
    year = diagnostic.supplement_impact(tmp_path)["by_year"]["2024"]
    assert year["filings"] == 4 and year["categories"]["capital_raise"] == 3
    assert year["non_bonus_capital_raise_decisions"] == 2 and year["hf001_hc003_original_rights_events"] == 1


def test_snapshot_opened_once_correct_shown_pools_and_cached_ranked_avoid_statistics(tmp_path, monkeypatch):
    fixture(tmp_path)
    root = snapshot(tmp_path)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    original = Path.open
    opened = []
    def track(path, *a, **k):
        if path.name == "screening_snapshot.json":
            opened.append(path)
        return original(path, *a, **k)
    monkeypatch.setattr(Path, "open", track)
    result = diagnostic.lh002_impact(tmp_path, {"000010"})
    assert len(opened) == 1
    decision = result["per_decision"][0]
    assert decision["universe"]["affected_all_zero"] == 1
    assert decision["arms"]["b_num"]["candidates"] == 2
    assert decision["arms"]["b_num"]["affected_share"] == 0.5
    assert result["by_arm"]["b_num"]["ranked"]["affected"] == 1
    assert result["by_arm"]["c_num"]["avoid"]["affected"] == 1
    context = decision["arms"]["c_num"]["calls"][0]["avoid"]
    assert context["hypergeometric_p_ge"] == pytest.approx(1 / 3)
    assert context["expected_affected"] == pytest.approx(1 / 3)
    assert result["snapshot_sha256"] == diagnostic.file_hash(root / "screening_snapshot.json")
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_invalid_and_missing_calls_are_unmeasured_not_zero_successes(tmp_path):
    root = snapshot(tmp_path)
    invalid = root / "calls" / ("a" * 64 + ".json")
    obj = json.loads(invalid.read_text())
    obj["valid"] = False
    invalid.write_text(json.dumps(obj))
    (root / "calls" / ("b" * 64 + ".json")).unlink()
    result = diagnostic.lh002_impact(tmp_path, {"000010"})
    assert result["by_arm"]["b_num"]["invalid_calls"] == 1
    assert result["by_arm"]["b_num"]["ranked"]["affected_share"] is None
    assert result["by_arm"]["c_num"]["missing_calls"] == 1


def test_stream_decoder_handles_chunk_boundaries_unicode_and_escapes():
    class Chunks(io.StringIO):
        def read(self, n):
            return super().read(min(13, n))
    value = {"data": {"observations": [{"n": 123456789, "text": '공시 [}]"\\'}] * 50,
                      "prepared": [{"bundle": {"decision_date": "2024-01-31"}, "texts": {}}]}, "metadata": {"key": "value"}}
    stream = diagnostic._JSONStream(Chunks(json.dumps(value, ensure_ascii=False)))
    assert list(stream.prepared()) == value["data"]["prepared"]
    assert not stream.peek()


def test_cli_dry_default_and_execute_only_publishes_new_reports(tmp_path, monkeypatch, capsys):
    fixture(tmp_path)
    # Keep publication inside this temporary repository; no real results touched.
    fake_script = tmp_path / "repo/scripts/research/impact.py"
    monkeypatch.setattr(diagnostic, "__file__", str(fake_script))
    assert diagnostic.main(["--data-dir", str(tmp_path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "dry_run" and not (tmp_path / "repo").exists()
    assert diagnostic.main(["--data-dir", str(tmp_path), "--execute"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "published"
    output = tmp_path / "repo/research/experiments/data_issues"
    assert len(list(output.glob("*.json"))) == len(list(output.glob("*.md"))) == 1


def test_publication_timestamp_collision_never_overwrites_results(tmp_path, monkeypatch, capsys):
    fixture(tmp_path)
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 14, tzinfo=timezone.utc)
    monkeypatch.setattr(diagnostic, "datetime", FixedDatetime)
    monkeypatch.setattr(diagnostic, "__file__", str(tmp_path / "repo/scripts/research/impact.py"))
    diagnostic.main(["--data-dir", str(tmp_path), "--execute"])
    output = tmp_path / "repo/research/experiments/data_issues"
    before = {p: p.read_bytes() for p in output.glob("*")}
    with pytest.raises(FileExistsError):
        diagnostic.main(["--data-dir", str(tmp_path), "--execute"])
    assert before == {p: p.read_bytes() for p in output.glob("*")}
