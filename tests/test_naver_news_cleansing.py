from datetime import datetime
from types import SimpleNamespace
import traceback

from bs4 import BeautifulSoup
import pytest

from src.ingestion.naver_news import NaverNewsCollector, match_news_entity
from src.ingestion.types import DocumentRecord


BODY = "삼성전자는 새로운 공급계약을 체결했다고 밝혔다. 계약에 대한 구체적인 내용은 별도 공시에서 확인할 수 있다."


def search_item(date="1시간 전", url="https://news.example/article/1"):
    return (f'<div class="news_area"><a class="news_tit" href="{url}">삼성전자 공급계약</a>'
            f'<span class="info">{date}</span></div>')


def article(publication="", content=None):
    return f'<h1>삼성전자 공급계약</h1>{publication}{content or f"<article>{BODY}</article>"}'


def collector_with_html(monkeypatch, search_html, article_html):
    collector = NaverNewsCollector()
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(text=search_html if url == collector.SEARCH_URL else article_html)

    monkeypatch.setattr(collector, "get_with_retry", get)
    monkeypatch.setattr("src.ingestion.naver_news.time.sleep", lambda _: None)
    return collector, calls


def test_limit_stops_search_before_fetching_next_page(monkeypatch):
    collector, calls = collector_with_html(monkeypatch, search_item(), article())
    assert len(collector.collect("삼성전자", max_items=1, max_pages=100)) == 1
    assert len(calls) == 2
    assert calls.count(collector.SEARCH_URL) == 1


def test_zero_requested_items_do_not_make_requests(monkeypatch):
    collector, calls = collector_with_html(monkeypatch, search_item(), article())
    assert collector.collect("삼성전자", max_items=0) == []
    assert calls == []


def test_repeated_page_does_not_repeat_article_fetch_or_scan_to_page_cap(monkeypatch):
    collector, calls = collector_with_html(monkeypatch, search_item(), article())
    assert len(collector.collect("삼성전자", max_items=20)) == 1
    assert calls.count(collector.SEARCH_URL) == 2
    assert len(calls) == 3


@pytest.mark.parametrize("publication,expected,precision", [
    ('<meta property="article:published_time" content="2026-09-05T10:12:30+09:00">', "2026-09-05T10:12:30+09:00", "datetime"),
    ('<meta itemprop="datePublished" content="2026-09-05T01:12:30Z">', "2026-09-05T01:12:30+00:00", "datetime"),
    ('<span class="media_end_head_info_datestamp_time _ARTICLE_DATE_TIME" data-date-time="2026-09-05 10:12:30"></span>', "2026-09-05T10:12:30+09:00", "datetime"),
    ('<time itemprop="datePublished" datetime="2026-09-05"></time>', "2026-09-05T00:00:00+09:00", "date"),
])
def test_actual_publication_is_used_instead_of_relative_search_time(monkeypatch, publication, expected, precision):
    collector, _ = collector_with_html(monkeypatch, search_item(), article(publication))
    document = collector.collect("삼성전자", max_items=1)[0]
    assert document.published_at == expected
    assert document.metadata["published_at_precision"] == precision
    assert document.metadata["publication_time_status"] == "confirmed"
    assert document.metadata["published_at_estimate"]
    assert datetime.fromisoformat(document.metadata["collected_at"]).tzinfo is not None


def test_relative_only_publication_is_not_promoted_to_confirmed_time(monkeypatch):
    collector, _ = collector_with_html(monkeypatch, search_item(), article())
    first, second = [collector.collect("삼성전자", max_items=1)[0] for _ in range(2)]
    assert first.published_at == second.published_at == ""
    assert first.ensure_doc_id() == second.ensure_doc_id()
    assert first.metadata["published_at_precision"] == "unknown"
    assert first.metadata["publication_time_status"] == "estimated"
    assert first.metadata["published_at_source"] == "search_relative"
    assert first.metadata["raw_date_text"] == "1시간 전"
    assert first.metadata["published_at_estimate"]


def test_absolute_search_date_keeps_date_precision(monkeypatch):
    collector, _ = collector_with_html(monkeypatch, search_item("2026.09.05."), article())
    document = collector.collect("삼성전자", max_items=1)[0]
    assert document.published_at == "2026-09-05T00:00:00+09:00"
    assert document.metadata["published_at_precision"] == "date"
    assert document.metadata["published_at_source"] == "search_absolute"


def test_article_without_search_date_can_still_supply_publication(monkeypatch):
    collector, _ = collector_with_html(monkeypatch, search_item(""),
        article('<meta property="article:published_time" content="2026-09-05T10:00:00+09:00">'))
    assert collector.collect("삼성전자", max_items=1)[0].published_at == "2026-09-05T10:00:00+09:00"


def test_missing_and_invalid_dates_are_not_replaced_by_modified_time(monkeypatch):
    collector, _ = collector_with_html(monkeypatch, search_item(""),
        article('<meta property="article:modified_time" content="2026-09-05T10:00:00+09:00">'))
    document = collector.collect("삼성전자", max_items=1)[0]
    assert document.published_at == ""
    assert document.metadata["publication_time_status"] == "missing"
    invalid = collector._extract_publication(BeautifulSoup(
        '<meta property="article:published_time" content="2026-99-05">', "html.parser"))
    assert invalid["published_at"] == ""
    assert invalid["publication_time_status"] == "invalid"


@pytest.mark.parametrize("content,scope,extracted,source", [
    (f"<article>{BODY}</article>", "document", True, "article"),
    (f'<meta property="og:description" content="{BODY}">', "summary", False, "meta_description"),
    (f"<p>{BODY}</p>", "available_fragments", False, "page_paragraphs"),
])
def test_body_summary_and_page_fragments_have_distinct_scopes(monkeypatch, content, scope, extracted, source):
    collector, _ = collector_with_html(monkeypatch, search_item(), article(content=content))
    document = collector.collect("삼성전자", max_items=1)[0]
    assert document.content == BODY
    assert document.metadata["evidence_scope"] == scope
    assert document.metadata["body_extracted"] is extracted
    assert document.metadata["body_source"] == source


@pytest.mark.parametrize("failure_stage", ["search", "article"])
def test_provider_failures_raise_sanitized_errors(monkeypatch, failure_stage):
    collector = NaverNewsCollector()

    def fail(url, **kwargs):
        if failure_stage == "article" and url == collector.SEARCH_URL:
            return SimpleNamespace(text=search_item())
        raise ValueError("https://example.test?api_key=secret-value")

    monkeypatch.setattr(collector, "get_with_retry", fail)
    with pytest.raises(RuntimeError, match=f"news_{failure_stage}.*failed") as captured:
        collector.collect("삼성전자", max_items=1)
    assert "secret-value" not in "".join(traceback.format_exception(captured.value))


@pytest.mark.parametrize("text,matched,method", [
    ("삼성전자는 공급계약을 발표했다.", True, "canonical_name"),
    ("삼성전자의 계약", True, "canonical_name"),
    ("삼성전자서비스의 계약", False, "none"),
    ("신삼성전자는 계약을 발표했다.", False, "none"),
    ("삼성의 새로운 계약", False, "none"),
    ("기업 (005930)의 계약", True, "explicit_stock_code"),
    ("종목코드: 005930 계약", True, "explicit_stock_code"),
    ("금액 005930원", False, "none"),
])
def test_entity_match_requires_full_name_or_explicit_ticker(text, matched, method):
    document = DocumentRecord(source_type="news", title="경제 뉴스", content=text, url="https://example.test/1")
    assert match_news_entity(document, "005930", "삼성전자") == {"matched": matched, "method": method}


def test_corporate_legal_prefix_does_not_require_it_in_article():
    document = DocumentRecord(source_type="news", title="삼성전자 계약", content=BODY, url="https://example.test/1")
    assert match_news_entity(document, "005930", "주식회사 삼성전자")["matched"] is True
