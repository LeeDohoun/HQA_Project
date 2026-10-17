"""Read-only access to sealed FW001 decision records. Nothing here recomputes a decision."""
from __future__ import annotations

import dataclasses
import json
import math
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

DECISION_DIR = Path("forward/fw001/decisions")
EXPERIMENT_ID = "FW001_forward_replication"
LABELS = ("forward", "reconstructed", "missing")
_FILE = re.compile(r"^\d{6}\.json$")
WEIGHT_TOLERANCE = 1e-6


class DecisionError(ValueError):
    """A decision record is garbled or violates the FW001 contract."""


def completion(data_dir, kind: str, month: str, multiplier=None):
    """FW001's own completion check (src.forward.fw001.completion_proof), read-only.

    Returns (proof, reason, error):
    - (proof, None, None): final (sealed in time);
    - (None, "<p>_missing", None): no record file;
    - (None, "<p>_not_sealed", None): record without a completion marker;
    - (None, "<p>_seal_late", None): marker published at/after the deadline (or within
      the one-second precision bound) - FW001 section 6: that month is missing/no-trade;
    - (None, "<p>_seal_invalid", message): corrupt or inconsistent evidence (visible error).
    <p> is "decision" or "execution".
    """
    from src.forward import fw001

    prefix = "decision" if kind == "decisions" else "execution"
    try:
        proof = fw001.completion_proof(data_dir, kind, month, multiplier)
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as error:
        return None, f"{prefix}_seal_invalid", str(error) or type(error).__name__
    if proof is not None:
        return proof, None, None
    if not fw001.path_for(data_dir, kind, month, multiplier).is_file():
        return None, f"{prefix}_missing", None
    if not fw001.marker_path(data_dir, kind, month, multiplier).is_file():
        return None, f"{prefix}_not_sealed", None
    return None, f"{prefix}_seal_late", None


def record_hash(record: dict) -> str:
    """FW001's ``digest`` (same code): the decision_hash stored in FW001 execution records."""
    from src.forward import fw001

    return fw001.digest(record)


@dataclass(frozen=True)
class Decision:
    rule: str
    month: str
    decision_date: str
    trade_date: str
    exit_date: str | None
    targets: tuple[str, ...]
    weights: dict = field(compare=False)
    record_hash: str
    label: str
    adv20: dict = field(default_factory=dict, compare=False, repr=False)
    sealed: bool = False
    seal_reason: str | None = None
    seal_error: str | None = field(default=None, compare=False)
    sealed_at: str | None = field(default=None, compare=False)

    @property
    def forward(self) -> bool:
        """Tradable: labelled forward AND sealed by a valid completion marker."""
        return self.label == "forward" and self.sealed


def _iso(value, name, path) -> str:
    if not isinstance(value, str):
        raise DecisionError(f"{path.name}: {name} must be an ISO date")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise DecisionError(f"{path.name}: {name} must be an ISO date") from None


def parse_decision(record: dict, rule: str, *, path: Path | None = None, execution: str = "basket_20") -> Decision:
    path = path or Path("<memory>")
    if not isinstance(record, dict):
        raise DecisionError(f"{path.name}: record must be an object")
    if record.get("experiment_id") != EXPERIMENT_ID or record.get("record_type") != "decision":
        raise DecisionError(f"{path.name}: not an FW001 decision record")
    label = record.get("label")
    if label not in LABELS:
        raise DecisionError(f"{path.name}: unknown label {label!r}")
    month = record.get("month")
    if not isinstance(month, str) or not re.fullmatch(r"\d{4}-\d{2}", month):
        raise DecisionError(f"{path.name}: month must be YYYY-MM")
    decision_date = _iso(record.get("decision_date"), "decision_date", path)
    trade_date = _iso(record.get("trade_date"), "trade_date", path)
    if decision_date[:7] != month or trade_date <= decision_date:
        raise DecisionError(f"{path.name}: decision/trade dates do not match the month")
    exit_date = None
    if execution == "basket_20":
        exit_date = _iso(record.get("f_both_exit"), "f_both_exit", path)
        if exit_date <= trade_date:
            raise DecisionError(f"{path.name}: f_both_exit must follow trade_date")
    rules = record.get("rules")
    targets: tuple[str, ...] = ()
    weights: dict = {}
    adv20: dict = {}
    if label == "forward" or isinstance(rules, dict) and rule in rules:
        if not isinstance(rules, dict) or not isinstance(rules.get(rule), dict):
            raise DecisionError(f"{path.name}: rules.{rule} is missing")
        body = rules[rule]
        raw_targets, raw_weights = body.get("targets"), body.get("weights")
        if not isinstance(raw_targets, list) or not isinstance(raw_weights, dict):
            raise DecisionError(f"{path.name}: rules.{rule} needs targets and weights")
        if any(not isinstance(code, str) or not code for code in raw_targets):
            raise DecisionError(f"{path.name}: invalid target code")
        if len(set(raw_targets)) != len(raw_targets):
            raise DecisionError(f"{path.name}: duplicate targets")
        if set(raw_targets) != set(raw_weights):
            raise DecisionError(f"{path.name}: targets and weight keys differ")
        for code, weight in raw_weights.items():
            if (isinstance(weight, bool) or not isinstance(weight, (int, float))
                    or not math.isfinite(weight) or weight <= 0):
                raise DecisionError(f"{path.name}: invalid weight for {code}")
        if raw_targets and abs(sum(raw_weights.values()) - 1.0) > WEIGHT_TOLERANCE:
            raise DecisionError(f"{path.name}: weights do not sum to 1")
        targets = tuple(sorted(raw_targets))
        weights = {code: float(raw_weights[code]) for code in targets}
        for row in body.get("universe") or []:
            if isinstance(row, dict) and isinstance(row.get("stock_code"), str):
                value = row.get("avg_trading_value_20d")
                if (not isinstance(value, bool) and isinstance(value, (int, float))
                        and math.isfinite(value) and value >= 0):
                    adv20[row["stock_code"]] = float(value)
    return Decision(rule=rule, month=month, decision_date=decision_date, trade_date=trade_date,
                    exit_date=exit_date, targets=targets, weights=weights,
                    record_hash=record_hash(record), label=label, adv20=adv20)


def load_decision(path: str | Path, rule: str, *, execution: str = "basket_20", data_dir=None) -> Decision:
    """Parse a record and apply FW001's completion check. Only a final (sealed in time)
    forward record trades; reconstructed records are never sealed and never trade."""
    path = Path(path)
    data_dir = Path(data_dir) if data_dir is not None else path.parents[3]
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DecisionError(f"{path.name}: unreadable decision record: {error}") from None
    decision = parse_decision(record, rule, path=path, execution=execution)
    if path.stem != decision.month.replace("-", ""):
        raise DecisionError(f"{path.name}: file name does not match month {decision.month}")
    from src.forward import fw001

    if decision.label != "forward" and not fw001.marker_path(data_dir, "decisions", decision.month).is_file():
        return decision
    proof, reason, error = completion(data_dir, "decisions", decision.month)
    if proof is None:
        return dataclasses.replace(decision, seal_reason=reason, seal_error=error)
    if proof.get("record_digest") != decision.record_hash:
        # The recorder writes encoded(record), so the byte digest equals the record digest.
        return dataclasses.replace(decision, seal_reason="decision_hash_marker_mismatch")
    return dataclasses.replace(decision, sealed=True, sealed_at=proof.get("sealed_at"))


def decision_files(data_dir: str | Path) -> list[Path]:
    """YYYYMM.json files only: dot files (temporaries) and reruns/ are never read."""
    directory = Path(data_dir) / DECISION_DIR
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_file() and _FILE.match(p.name))


def list_decisions(data_dir: str | Path, rule: str, *, execution: str = "basket_20") -> list[Decision]:
    """Every decision record (any label), oldest first. Only forward ones may trade."""
    return [load_decision(path, rule, execution=execution, data_dir=data_dir) for path in decision_files(data_dir)]


def decision_for_month(data_dir, rule, month: str, *, execution: str = "basket_20") -> Decision | None:
    path = Path(data_dir) / DECISION_DIR / (month.replace("-", "") + ".json")
    return load_decision(path, rule, execution=execution, data_dir=data_dir) if path.is_file() else None


def latest_decision(data_dir, rule, as_of_date, *, execution: str = "basket_20",
                    forward_only: bool = True) -> Decision | None:
    """Latest decision whose decision_date <= as_of_date (forward-labelled by default)."""
    as_of = date.fromisoformat(str(as_of_date)[:10]).isoformat()
    found = None
    for decision in list_decisions(data_dir, rule, execution=execution):
        if decision.decision_date <= as_of and (decision.forward or not forward_only):
            found = decision
    return found
