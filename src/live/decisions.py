"""Read-only access to sealed FW001 decision records. Nothing here recomputes a decision."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

DECISION_DIR = Path("forward/fw001/decisions")
SEAL_DIR = Path("forward/fw001/sealed")
EXPERIMENT_ID = "FW001_forward_replication"
LABELS = ("forward", "reconstructed", "missing")
_FILE = re.compile(r"^\d{6}\.json$")
WEIGHT_TOLERANCE = 1e-6


class DecisionError(ValueError):
    """A decision record is garbled or violates the FW001 contract."""


class SealError(ValueError):
    """A completion marker is missing or does not seal the record being read."""

    def __init__(self, reason, detail=""):
        super().__init__(f"{reason}:{detail}" if detail else reason)
        self.reason = reason


def decision_marker_path(data_dir, month) -> Path:
    return Path(data_dir) / SEAL_DIR / f"{month.replace('-', '')}.decision.json"


def execution_marker_path(data_dir, month, multiplier="1.0") -> Path:
    return Path(data_dir) / SEAL_DIR / f"{month.replace('-', '')}.execution.{multiplier}.json"


def _aware(value):
    stamp = datetime.fromisoformat(value) if isinstance(value, str) else None
    if stamp is None or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("timestamp must be an aware ISO 8601 string")
    return stamp


def parse_seal_marker(raw) -> dict:
    """The FW001 completion-marker field contract, isolated so it can follow the recorder.

    Fields: record_path, record_digest (sha256 hex of the record file bytes),
    sealed_at and deadline (aware ISO), reconstructed (must be false).
    Raises ValueError for a malformed marker.
    """
    if not isinstance(raw, dict):
        raise ValueError("marker must be an object")
    path, digest = raw.get("record_path"), raw.get("record_digest")
    if not isinstance(path, str) or not path:
        raise ValueError("record_path must be a nonempty string")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("record_digest must be 64 lowercase hex characters")
    if not isinstance(raw.get("reconstructed"), bool):
        raise ValueError("reconstructed must be a boolean")
    return {"record_path": path, "record_digest": digest, "sealed_at": _aware(raw.get("sealed_at")),
            "deadline": _aware(raw.get("deadline")), "reconstructed": raw["reconstructed"]}


def verify_seal(data_dir, record_path, record_bytes: bytes, marker_path, kind: str) -> str:
    """Return the marker's record_digest when it validly seals these bytes; raise SealError otherwise.

    Reasons: <kind>_not_sealed, <kind>_seal_invalid, <kind>_seal_path_mismatch,
    <kind>_seal_digest_mismatch, <kind>_seal_reconstructed, <kind>_seal_after_deadline.
    """
    marker_path = Path(marker_path)
    if not marker_path.is_file():
        raise SealError(f"{kind}_not_sealed")
    try:
        marker = parse_seal_marker(json.loads(marker_path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as error:  # JSONDecodeError is a ValueError
        raise SealError(f"{kind}_seal_invalid", str(error)) from None
    sealed = Path(marker["record_path"])
    sealed = sealed if sealed.is_absolute() else Path(data_dir) / sealed
    if sealed.resolve() != Path(record_path).resolve():
        raise SealError(f"{kind}_seal_path_mismatch")
    if hashlib.sha256(record_bytes).hexdigest() != marker["record_digest"]:
        raise SealError(f"{kind}_seal_digest_mismatch")
    if marker["reconstructed"]:
        raise SealError(f"{kind}_seal_reconstructed")
    if not marker["sealed_at"] < marker["deadline"]:
        raise SealError(f"{kind}_seal_after_deadline")
    return marker["record_digest"]


def record_hash(record: dict) -> str:
    """sha256 of the whole immutable record, encoded as FW001's ``digest`` does.

    Matching the recorder's encoding lets a batch's decisionHash be compared
    with the decision_hash stored in FW001 execution records.
    """
    text = json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
    """Parse a record and check its completion marker. Only label forward is checked;
    reconstructed records never get markers and never trade."""
    path = Path(path)
    data_dir = Path(data_dir) if data_dir is not None else path.parents[3]
    try:
        raw = path.read_bytes()
        record = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DecisionError(f"{path.name}: unreadable decision record: {error}") from None
    decision = parse_decision(record, rule, path=path, execution=execution)
    if path.stem != decision.month.replace("-", ""):
        raise DecisionError(f"{path.name}: file name does not match month {decision.month}")
    if decision.label != "forward":
        return decision
    try:
        digest = verify_seal(data_dir, path, raw, decision_marker_path(data_dir, decision.month), "decision")
    except SealError as error:
        return dataclasses.replace(decision, seal_reason=error.reason)
    if digest != decision.record_hash:
        return dataclasses.replace(decision, seal_reason="decision_hash_marker_mismatch")
    return dataclasses.replace(decision, sealed=True)


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
