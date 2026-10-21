"""Frozen v1 UTF-8 JSON sections; null is always rendered as 자료 없음."""
import json

SECTIONS = ("decision_day", "as_of", "universe", "candidates", "disclosures", "market", "ledger", "rules")


def render(snapshot):
    def missing(value):
        if value is None:
            return "자료 없음"
        if isinstance(value, dict):
            return {key: missing(item) for key, item in value.items()}
        if isinstance(value, list):
            return [missing(item) for item in value]
        return value
    return "\n".join(f"[{key}]\n" + json.dumps(missing(snapshot[key]), ensure_ascii=False,
                       sort_keys=True, separators=(",", ":"), allow_nan=False) for key in SECTIONS)
