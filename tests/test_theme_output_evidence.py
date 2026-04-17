from __future__ import annotations

import json

from main import _save_theme_analysis_report
from src.agents.theme_orchestrator import ThemeLeaderOrchestrator
from src.config.settings import reset_settings_cache


def test_extract_evidence_from_packet_and_merge():
    packet = {
        "evidence": [
            {
                "source": "news",
                "title": "에코프로 수혜",
                "snippet": "양극재 수요 확대",
                "url": "",
                "note": "2026-04-01",
            },
            {
                "source": "news",
                "title": "에코프로 수혜",
                "snippet": "양극재 수요 확대",
                "url": "",
                "note": "2026-04-01",
            },
        ]
    }

    rows = ThemeLeaderOrchestrator._extract_evidence_from_packet(packet)
    merged = ThemeLeaderOrchestrator._merge_evidence([rows])

    assert len(rows) == 2
    assert len(merged) == 1
    assert merged[0]["title"] == "에코프로 수혜"


def test_save_theme_analysis_report_writes_json(tmp_path, monkeypatch):
    monkeypatch.setenv("HQA_DATA_DIR", str(tmp_path))
    reset_settings_cache()

    result = {
        "status": "success",
        "theme": "2차전지",
        "theme_key": "2차전지",
        "leaders": [
            {
                "candidate": {"stock_name": "에코프로", "stock_code": "086520"},
                "final_decision": {"action": "매수", "confidence": 75},
                "evidence": [{"source": "news", "title": "샘플", "snippet": "근거"}],
            }
        ],
    }

    report_path = _save_theme_analysis_report(result)
    assert report_path is not None

    saved = json.loads(open(report_path, "r", encoding="utf-8").read())
    assert saved["theme_key"] == "2차전지"
    assert saved["leaders"][0]["evidence"][0]["title"] == "샘플"
