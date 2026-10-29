from scripts.data.common import collection_status
from src.ingestion.claude_news import ClaudeNewsCollector

CONFIRMED = {"published_at": "2026-10-20T09:00:00+09:00", "published_at_source": "meta[property='article:published_time']",
             "published_at_precision": "datetime", "publication_time_status": "confirmed"}
MISSING = {"published_at": "", "published_at_source": "none", "published_at_precision": "unknown",
           "publication_time_status": "missing"}


def test_claude_finds_articles_but_only_pages_that_state_their_time_are_kept(monkeypatch):
    collector = ClaudeNewsCollector()
    found = [{"url": url, "title": "삼성전자 공급계약", "press": "언론사", "published_date": "2026-10-20", "summary": "요약"}
             for url in ("https://news.example/a", "https://news.example/b", "javascript:alert(1)")]
    monkeypatch.setattr(collector, "_search", lambda keyword, from_date, to_date: found)
    pages = {"https://news.example/a": CONFIRMED, "https://news.example/b": MISSING}
    fetched = []

    def fetch(url):
        fetched.append(url)
        return {"title": "삼성전자 공급계약 체결", "content": "삼성전자는 대규모 반도체 공급계약을 체결했다고 20일 공시를 통해 밝혔다.",
                "evidence_scope": "document", "body_source": "article", "body_extracted": True,
                "publication": pages[url]}

    monkeypatch.setattr(collector, "_fetch_article_detail", fetch)
    docs = collector.collect("삼성전자 005930 주식", max_items=8, from_date="20261015", to_date="20261021")
    # A non-web URL is never fetched; Claude's own date never stands in for the page's time.
    assert fetched == ["https://news.example/a", "https://news.example/b"]
    assert [doc.url for doc in docs] == ["https://news.example/a"]
    assert docs[0].published_at == CONFIRMED["published_at"]


def test_a_news_outage_does_not_hold_back_prices():
    report = {"enabled_sources": ["news", "kis_chart"], "source_success": {"news": False, "kis_chart": True},
              "source_status": {"news": "error", "kis_chart": "success"}, "failures": {"news": "HTTP 403"}}
    assert collection_status([report]) == "done"
    report["source_success"]["kis_chart"] = False
    report["source_status"]["kis_chart"] = "error"
    report["failures"]["kis_chart"] = "HTTP 500"
    assert collection_status([report]) == "error"
