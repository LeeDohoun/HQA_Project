from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


class ThemeEvidenceBuilder:
    """Build compact, loggable evidence cards for LLM theme decisions."""

    def build_theme_evidence(
        self,
        *,
        theme: str,
        theme_key: str,
        filtered_result: Dict[str, Any],
        max_cards: int,
    ) -> Dict[str, Any]:
        passed = list(filtered_result.get("passed") or [])
        ranked = sorted(
            passed,
            key=lambda row: (
                float((row.get("features") or {}).get("avg_trading_value_20d") or 0.0),
                float((row.get("features") or {}).get("return_20d") or 0.0),
                str(row.get("stock_code") or ""),
            ),
            reverse=True,
        )
        if max_cards > 0:
            ranked = ranked[:max_cards]

        return {
            "theme": theme,
            "theme_key": theme_key,
            "evidence_cards": [self._build_card(theme, theme_key, row) for row in ranked],
            "filter_summary": {
                "passed_count": int(filtered_result.get("passed_count") or len(passed)),
                "rejected_count": int(filtered_result.get("rejected_count") or len(filtered_result.get("rejected") or [])),
            },
        }

    def _build_card(self, theme: str, theme_key: str, row: Dict[str, Any]) -> Dict[str, Any]:
        candidate = row.get("candidate") or {}
        documents = list(candidate.get("documents") or [])
        source_counts = dict(row.get("source_counts") or candidate.get("source_counts") or {})
        features = dict(row.get("features") or {})
        return {
            "theme": theme,
            "theme_key": theme_key,
            "stock_code": row.get("stock_code"),
            "stock_name": row.get("stock_name"),
            "price_features": features,
            "document_evidence": {
                "news_count": int(source_counts.get("news") or 0),
                "dart_count": int(source_counts.get("dart") or 0),
                "forum_count": int(source_counts.get("forum") or 0),
                "theme_fit_summary": self._summary_for(documents, preferred=("dart", "news", "forum")),
                "catalyst_summary": self._summary_for(documents, preferred=("dart", "news")),
                "risk_summary": self._risk_summary(features),
            },
            "filter_summary": {
                "passed": True,
                "notes": [],
            },
        }

    def _summary_for(self, documents: List[Dict[str, Any]], *, preferred: Iterable[str]) -> str:
        for source in preferred:
            doc = self._first_document(documents, source)
            if doc:
                text = self._document_text(doc)
                if text:
                    return self._compact(text)
        return "No compact evidence available"

    def _first_document(self, documents: List[Dict[str, Any]], source: str) -> Optional[Dict[str, Any]]:
        source = source.lower()
        for doc in documents:
            source_type = self._source_type(doc)
            if source == "dart" and ("dart" in source_type or "disclosure" in source_type):
                return doc
            if source == "news" and ("news" in source_type or "article" in source_type):
                return doc
            if source == "forum" and ("forum" in source_type or "community" in source_type or "board" in source_type):
                return doc
        return None

    @staticmethod
    def _source_type(doc: Dict[str, Any]) -> str:
        metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        return str(doc.get("source_type") or metadata.get("source_type") or metadata.get("source") or "").lower()

    @staticmethod
    def _document_text(doc: Dict[str, Any]) -> str:
        metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        parts = []
        for key in ("title", "summary", "content", "text"):
            value = doc.get(key) or metadata.get(key)
            if value and str(value).strip():
                parts.append(str(value).strip())
        return " ".join(parts)

    @staticmethod
    def _compact(text: str, limit: int = 220) -> str:
        normalized = " ".join(str(text or "").split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 3].rstrip() + "..."

    @staticmethod
    def _risk_summary(features: Dict[str, Any]) -> str:
        risks = []
        return_5d = features.get("return_5d")
        return_20d = features.get("return_20d")
        volatility = features.get("volatility_20d")
        try:
            if return_5d is not None and float(return_5d) >= 0.25:
                risks.append("5d return is elevated")
            if return_20d is not None and float(return_20d) >= 0.6:
                risks.append("20d return is elevated")
            if volatility is not None and float(volatility) >= 0.08:
                risks.append("20d volatility is elevated")
        except (TypeError, ValueError):
            pass
        return "; ".join(risks) if risks else "No major short-term overheating flag in computed features"
