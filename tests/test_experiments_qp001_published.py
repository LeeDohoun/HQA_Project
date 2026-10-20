"""The large published replay input is projected before JSON decoding."""
import gzip
import json

import pytest

from backtesting.experiments.qp001.published import read_av_window


def test_only_three_published_months_are_decoded_per_rule(tmp_path, monkeypatch):
    path = tmp_path / "av.json.gz"
    saved = {"rules": {}, "selections": {}, "source_hashes": {"prices": {"market/krx_daily/2017/20170601.jsonl": "synthetic_sha"}}}
    for rule in ("S", "O"):
        saved["selections"][rule] = [{"month": f"2017-{i:02d}", "U": ["000010"], "H": {"av_all": ["000010"]}} for i in range(5, 9)]
        saved["rules"][rule] = {"av_all": {"per_month": [{"month": f"2017-{i:02d}", "excess": i / 100} for i in range(5, 9)]}}
        saved["rules"][rule]["av_all"]["per_month"][-1]["NEVER_DECODE"] = "later-month"
    with gzip.open(path, "wt") as handle:
        json.dump(saved, handle, indent=2)
    original = json.loads
    def bounded(value, **kwargs):
        assert "NEVER_DECODE" not in value
        return original(value, **kwargs)
    monkeypatch.setattr(json, "loads", bounded)
    result = read_av_window(path)
    for rule in ("S", "O"):
        assert len(result["selections"][rule]) == 3
        assert len(result["observations"][rule]) == 3
    assert result["prices"] == saved["source_hashes"]["prices"]


def test_unknown_published_structure_fails_instead_of_loading_full_json(tmp_path):
    path = tmp_path / "wrong.json.gz"
    with gzip.open(path, "wt") as handle:
        json.dump({"wrong": {}}, handle, indent=2)
    with pytest.raises(ValueError, match="structure differs"):
        read_av_window(path)
