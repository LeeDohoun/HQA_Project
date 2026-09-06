from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.ingestion.dart import DartDisclosureCollector
from src.ingestion.dart_api import DartAPIError


def row(number, title="Other filing"):
    return {"rcept_no": f"20260904{number:06d}", "rcept_dt": "20260904",
            "report_nm": title, "corp_code": "00126380", "corp_name": "Example"}


def page(number, rows, total, size=100):
    return {"status": "000", "page_no": number, "page_count": size,
            "total_count": total, "total_page": (total + size - 1) // size, "list": rows}


def collector_with(*payloads):
    collector = DartDisclosureCollector("fixture-private-key")
    collector.get_with_retry = Mock(side_effect=[SimpleNamespace(json=lambda value=value: value) for value in payloads])
    return collector


def test_all_pages_are_fetched_before_importance_filter(monkeypatch):
    collector = collector_with(page(1, [row(n) for n in range(100)], 101),
                               page(2, [row(100, "사업보고서")], 101))
    collector._fetch_structured_body = Mock(return_value=("", "structured_api", {}))
    collector._fetch_official_document_body = Mock(return_value=("", "official_api", {}))
    collector._fetch_detail_excerpt = Mock(return_value=("", "viewer", {}))
    monkeypatch.setattr("src.ingestion.dart.time.sleep", lambda _: None)
    docs = collector.collect("00126380", "20260901", "20260905")
    assert len(docs) == 1
    assert docs[0].metadata["rcept_no"] == row(100)["rcept_no"]
    assert [call.kwargs["params"]["page_no"] for call in collector.get_with_retry.call_args_list] == [1, 2]
    assert all(call.kwargs["params"]["last_reprt_at"] == "N" for call in collector.get_with_retry.call_args_list)


def test_identical_receipts_are_deduplicated_across_pages():
    collector = collector_with(page(1, [row(1), row(2)], 3, 2), page(2, [row(1)], 3, 2))
    rows = collector._collect_listing("00126380", "20260901", "20260905", 2)
    assert [item["rcept_no"] for item in rows] == [row(1)["rcept_no"], row(2)["rcept_no"]]


@pytest.mark.parametrize("second", [
    {"status": "013"},
    page(2, [], 3, 2),
    page(1, [row(3)], 3, 2),
    page(2, [row(3), row(4)], 4, 2),
    page(2, [{"rcept_no": "invalid"}], 3, 2),
    page(2, [row(1, "Conflicting title")], 3, 2),
])
def test_incomplete_changed_or_malformed_later_page_fails_whole_collection(second):
    collector = collector_with(page(1, [row(1), row(2)], 3, 2), second)
    with pytest.raises(DartAPIError):
        collector.collect("00126380", "20260901", "20260905", page_count=2)


@pytest.mark.parametrize("payload", [
    {"status": "020", "message": "fixture-private-key https://provider.invalid?crtfc_key=fixture-private-key"},
    {"status": "010"}, {"status": "800"}, {"status": "014"}, {}, [],
    {"status": "000", "list": []}, {"status": "013", "list": [row(1)]},
])
def test_only_explicit_no_data_is_empty_and_provider_errors_are_sanitized(payload):
    collector = collector_with(payload)
    with pytest.raises(DartAPIError) as error:
        collector.collect("00126380", "20260901", "20260905")
    assert "fixture-private-key" not in str(error.value)
    assert "https://" not in str(error.value)


def test_genuine_no_data_and_invalid_configuration():
    assert collector_with({"status": "013"}).collect("00126380", "20260901", "20260905") == []
    collector = collector_with()
    with pytest.raises(ValueError, match="page_count"):
        collector.collect("00126380", "20260901", "20260905", page_count=101)
    collector.get_with_retry.assert_not_called()


def test_structured_provider_error_is_not_cached_or_replaced_with_empty():
    collector = collector_with({"status": "020", "message": "fixture-private-key"})
    with pytest.raises(DartAPIError, match="status=020"):
        collector._fetch_structured_body("00126380", "20260901", "20260905",
                                        "20260904000001", "주요사항보고서(유상증자결정)")
    assert collector._structured_cache == {}


def test_transport_error_never_propagates_request_url():
    collector = collector_with()
    collector.get_with_retry.side_effect = RuntimeError("https://provider.invalid?crtfc_key=fixture-private-key")
    with pytest.raises(DartAPIError, match="transport failure") as error:
        collector.collect("00126380", "20260901", "20260905")
    assert "fixture-private-key" not in str(error.value)
    assert error.value.__suppress_context__
