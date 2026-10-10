import inspect

import src.ingestion as ingestion
from src.ingestion.services import IngestionService


def test_ingestion_public_surface_excludes_legacy_kis_collectors():
    assert "KISClient" not in ingestion.__all__
    assert "KISChartCollector" not in ingestion.__all__
    assert not hasattr(ingestion, "KISClient")
    assert not hasattr(ingestion, "KISChartCollector")


def test_kis_daily_prices_are_an_opt_in_source_not_a_default_chart_dependency():
    # The removed collector was a default dependency that served adjusted, unpaginated KIS bars.
    # KIS bars now come only from the explicit kis_chart source (tests/test_kis_chart_source.py);
    # the default service never builds a KIS collector or reads broker credentials.
    signature = inspect.signature(IngestionService)

    assert signature.parameters["kis_chart_collector"].default is None
    assert IngestionService().kis_chart_collector is None
