from __future__ import annotations

import time
from pathlib import Path
import os

import pytest

import scripts.data.collect as theme_pipeline


def _write_corp_codes(path: Path) -> None:
    path.write_text(
        "stock_code,corp_code,corp_name,modify_date\n"
        "005930,00126380,삼성전자,20240101\n",
        encoding="utf-8-sig",
    )


def test_ensure_corp_codes_downloads_when_file_is_missing(tmp_path, monkeypatch):
    csv_path = tmp_path / "corp_codes.csv"
    calls = []

    def fake_refresh(path: str) -> None:
        calls.append(path)
        _write_corp_codes(Path(path))

    monkeypatch.setenv("DART_API_KEY", "test-key")
    monkeypatch.setattr(theme_pipeline, "_refresh_corp_codes_csv", fake_refresh)

    assert theme_pipeline._ensure_fresh_corp_codes_csv(str(csv_path), max_age_days=7) is True

    assert calls == [str(csv_path)]
    assert csv_path.exists()


def test_ensure_corp_codes_downloads_when_file_is_stale(tmp_path, monkeypatch):
    csv_path = tmp_path / "corp_codes.csv"
    _write_corp_codes(csv_path)
    old_timestamp = time.time() - (8 * 24 * 60 * 60)
    os.utime(csv_path, (old_timestamp, old_timestamp))
    calls = []

    def fake_refresh(path: str) -> None:
        calls.append(path)
        _write_corp_codes(Path(path))

    monkeypatch.setenv("DART_API_KEY", "test-key")
    monkeypatch.setattr(theme_pipeline, "_refresh_corp_codes_csv", fake_refresh)

    assert theme_pipeline._ensure_fresh_corp_codes_csv(str(csv_path), max_age_days=7) is True

    assert calls == [str(csv_path)]


def test_ensure_corp_codes_keeps_fresh_file(tmp_path, monkeypatch):
    csv_path = tmp_path / "corp_codes.csv"
    _write_corp_codes(csv_path)

    def fail_if_called(path: str) -> None:
        raise AssertionError(f"unexpected refresh: {path}")

    monkeypatch.setenv("DART_API_KEY", "test-key")
    monkeypatch.setattr(theme_pipeline, "_refresh_corp_codes_csv", fail_if_called)

    assert theme_pipeline._ensure_fresh_corp_codes_csv(str(csv_path), max_age_days=7) is False


def test_ensure_corp_codes_keeps_existing_file_when_refresh_fails(tmp_path, monkeypatch):
    csv_path = tmp_path / "corp_codes.csv"
    _write_corp_codes(csv_path)
    old_timestamp = time.time() - (8 * 24 * 60 * 60)
    os.utime(csv_path, (old_timestamp, old_timestamp))

    def fail_refresh(path: str) -> None:
        raise RuntimeError("dart unavailable")

    monkeypatch.setenv("DART_API_KEY", "test-key")
    monkeypatch.setattr(theme_pipeline, "_refresh_corp_codes_csv", fail_refresh)

    assert theme_pipeline._ensure_fresh_corp_codes_csv(str(csv_path), max_age_days=7) is False
    assert "00126380" in csv_path.read_text(encoding="utf-8-sig")


def test_ensure_corp_codes_missing_file_without_api_key_does_not_create(tmp_path, monkeypatch):
    csv_path = tmp_path / "corp_codes.csv"
    monkeypatch.delenv("DART_API_KEY", raising=False)

    assert theme_pipeline._ensure_fresh_corp_codes_csv(str(csv_path), max_age_days=7) is False
    assert not csv_path.exists()
