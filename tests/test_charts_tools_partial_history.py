from datetime import date, timedelta
import json

from src.tools.charts_tools import TechnicalAnalyzer


def _write_chart_rows(path, stock_code: str, rows: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    start = date(2026, 1, 1)
    with path.open("w", encoding="utf-8") as f:
        for index in range(rows):
            day = start + timedelta(days=index)
            close = 10000 + index * 10
            row = {
                "source_type": "chart",
                "stock_name": "테스트",
                "stock_code": stock_code,
                "timestamp": f"{day.isoformat()}T00:00:00",
                "open": str(close - 20),
                "high": str(close + 50),
                "low": str(close - 50),
                "close": str(close),
                "volume": str(100000 + index),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_analyze_uses_available_history_when_at_least_sixty_rows(tmp_path):
    _write_chart_rows(tmp_path / "market_data" / "theme" / "chart.jsonl", "005930", 60)

    result = TechnicalAnalyzer(data_dir=str(tmp_path)).analyze("005930", "삼성전자")

    assert result.stock_code == "005930"
    assert result.date == "2026-03-01"
    assert result.current_price == 10590
    assert result.ma150 > 0
