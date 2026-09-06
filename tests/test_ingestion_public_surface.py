import inspect

import src.ingestion as ingestion
from src.ingestion.services import IngestionService


def test_ingestion_public_surface_excludes_legacy_kis_collectors():
    assert "KISClient" not in ingestion.__all__
    assert "KISChartCollector" not in ingestion.__all__
    assert not hasattr(ingestion, "KISClient")
    assert not hasattr(ingestion, "KISChartCollector")


def test_ingestion_service_constructor_excludes_legacy_kis_chart_dependency():
    signature = inspect.signature(IngestionService)

    assert "kis_chart_collector" not in signature.parameters
