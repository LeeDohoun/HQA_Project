"""Strategy book and stage approvals: strict readers, no defaults guessed."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml

STAGES = ("observe", "shadow", "paper")
EXECUTIONS = ("basket_20", "continuous", "continuous_intraday")
# Executions that rebalance continuously against target weights (band required).
BANDED_EXECUTIONS = ("continuous", "continuous_intraday")
COST_MODEL_VERSIONS = ("v1", "v2")
DECISION_KINDS = ("fw001",)
DECISION_RULES = ("f_both", "av_all")
APPROVAL_STAGES = ("paper",)
BOOK_PATH = Path("config/strategy_book.yaml")
APPROVALS_PATH = Path("config/strategy_approvals.yaml")
STRATEGY_ID = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")

_REQUIRED = {"title", "description", "experiment_id", "variant", "holdout", "decision_source",
             "execution", "cost_model_version", "requested_stage", "shadow_nav_krw", "account_user_id"}
_OPTIONAL = {"band", "preregistration", "early_paper", "sleeves", "limits", "t0"}
_LIMIT_KEYS = {"max_name_weight", "max_adv_share"}
_SLEEVE_KEYS = {"weight", "source"}
_EARLY_PAPER_KEYS = {"decided_by", "decided_at", "reason"}
_APPROVAL_KEYS = {"strategy_id", "stage", "approved_by", "approved_at", "evidence"}


class BookError(ValueError):
    """The strategy book or approval file is missing, garbled or invalid."""


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys instead of keeping the last."""


def _mapping(loader, node, deep=False):
    keys = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in keys:
            raise BookError(f"duplicate key: {key}")
        keys.add(key)
    return loader.construct_mapping(node, deep=deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _load_yaml(path: Path):
    if not path.is_file():
        raise BookError(f"missing file: {path}")
    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=_StrictLoader)
    except yaml.YAMLError as error:
        raise BookError(f"invalid YAML: {path}: {error}") from None


def stage_rank(stage: str) -> int:
    if stage not in STAGES:
        raise BookError(f"unknown stage: {stage}")
    return STAGES.index(stage)


@dataclass(frozen=True)
class StrategyEntry:
    strategy_id: str
    title: str
    description: str
    experiment_id: str
    variant: str
    holdout_experiment_ids: tuple[str, ...]
    holdout_variants: tuple[str, ...]
    decision_kind: str
    decision_rule: str
    execution: str
    cost_model_version: str
    band: float | None
    requested_stage: str
    shadow_nav_krw: int
    account_user_id: str | None
    preregistration: str | None
    # Explicit user decision to run PAPER (mock) before the roadmap gates: {decided_by, decided_at, reason}.
    # It never applies to real-money stages, which this runtime does not have.
    early_paper: dict | None = None
    # continuous_intraday only: capital sleeves and per-name limits.
    sleeves: tuple = ()
    limits: dict | None = None
    t0: str | None = None  # fixed start session (OP001: 2026-11-02), never sliding

    @property
    def traded_sleeve(self):
        """The sleeve whose source is the book's decision_source (the only one that trades now)."""
        return next((s for s in self.sleeves if s.source is not None), None)


@dataclass(frozen=True)
class Sleeve:
    name: str
    weight: float
    # {"kind", "rule"} or None: a sleeve without a source holds cash until its source exists.
    # Extension point: a future source kind (e.g. a sealed OP001 combined-target record) is
    # added to DECISION_KINDS and read by the runtime; nothing here needs to change.
    source: dict | None


def _text(value, name, sid) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BookError(f"{sid}: {name} must be a nonempty string")
    return value


def _names(value, name, sid, *, allow_empty=False) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise BookError(f"{sid}: {name} must be a nonempty list")
    items = tuple(_text(item, name, sid) for item in value)
    if len(set(items)) != len(items):
        raise BookError(f"{sid}: {name} has duplicates")
    return items


def _fraction(value, *, upper_inclusive=True) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return False
    return 0 <= value <= 1 if upper_inclusive else 0 <= value < 1


def _sleeves_and_limits(sid, raw, execution, source):
    """continuous_intraday requires sleeves and limits; other executions must not have them."""
    if execution != "continuous_intraday":
        if "sleeves" in raw or "limits" in raw or "t0" in raw:
            raise BookError(f"{sid}: sleeves, limits and t0 apply only to continuous_intraday")
        return (), None
    sleeves_raw, limits_raw = raw.get("sleeves"), raw.get("limits")
    if not isinstance(limits_raw, dict) or set(limits_raw) != _LIMIT_KEYS:
        raise BookError(f"{sid}: limits must have exactly {sorted(_LIMIT_KEYS)}")
    for key in _LIMIT_KEYS:
        if not _fraction(limits_raw[key]) or limits_raw[key] == 0:
            raise BookError(f"{sid}: limits.{key} must be in (0, 1]")
    if not isinstance(sleeves_raw, dict) or not sleeves_raw:
        raise BookError(f"{sid}: continuous_intraday requires a nonempty sleeves mapping")
    sleeves = []
    for name, body in sleeves_raw.items():
        if not isinstance(name, str) or not STRATEGY_ID.match(name):
            raise BookError(f"{sid}: invalid sleeve name {name!r}")
        if not isinstance(body, dict) or set(body) != _SLEEVE_KEYS:
            raise BookError(f"{sid}: sleeve {name} must have exactly {sorted(_SLEEVE_KEYS)}")
        if not _fraction(body["weight"]) or body["weight"] == 0:
            raise BookError(f"{sid}: sleeve {name} weight must be in (0, 1]")
        sleeve_source = body["source"]
        if sleeve_source is not None:
            if (not isinstance(sleeve_source, dict) or set(sleeve_source) != {"kind", "rule"}
                    or sleeve_source["kind"] not in DECISION_KINDS or sleeve_source["rule"] not in DECISION_RULES):
                raise BookError(f"{sid}: sleeve {name} source must be null or a known {{kind, rule}}")
            sleeve_source = {"kind": sleeve_source["kind"], "rule": sleeve_source["rule"]}
        sleeves.append(Sleeve(name, float(body["weight"]), sleeve_source))
    if sum(s.weight for s in sleeves) > 1 + 1e-9:
        raise BookError(f"{sid}: sleeve weights sum above 1")
    sourced = [s for s in sleeves if s.source is not None]
    if len(sourced) != 1 or sourced[0].source != {"kind": source["kind"], "rule": source["rule"]}:
        raise BookError(f"{sid}: exactly one sleeve must trade, with the book's decision_source")
    return tuple(sleeves), {key: float(limits_raw[key]) for key in sorted(_LIMIT_KEYS)}


def _t0(sid, raw, execution):
    if execution != "continuous_intraday":
        return None
    value = raw.get("t0")
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise BookError(f"{sid}: continuous_intraday requires t0 as YYYY-MM-DD") from None


def _entry(sid, raw) -> StrategyEntry:
    if not isinstance(sid, str) or not STRATEGY_ID.match(sid):
        raise BookError(f"invalid strategy id: {sid!r}")
    if not isinstance(raw, dict):
        raise BookError(f"{sid}: entry must be a mapping")
    keys = set(raw)
    if keys - _REQUIRED - _OPTIONAL:
        raise BookError(f"{sid}: unknown keys {sorted(keys - _REQUIRED - _OPTIONAL)}")
    if _REQUIRED - keys:
        raise BookError(f"{sid}: missing keys {sorted(_REQUIRED - keys)}")
    holdout = raw["holdout"]
    if not isinstance(holdout, dict) or set(holdout) != {"experiment_ids", "variants"}:
        raise BookError(f"{sid}: holdout must have exactly experiment_ids and variants")
    source = raw["decision_source"]
    if not isinstance(source, dict) or set(source) != {"kind", "rule"}:
        raise BookError(f"{sid}: decision_source must have exactly kind and rule")
    if source["kind"] not in DECISION_KINDS:
        raise BookError(f"{sid}: unknown decision_source.kind {source['kind']!r}")
    if source["rule"] not in DECISION_RULES:
        raise BookError(f"{sid}: unknown decision_source.rule {source['rule']!r}")
    execution = raw["execution"]
    if execution not in EXECUTIONS:
        raise BookError(f"{sid}: unknown execution {execution!r}")
    if raw["cost_model_version"] not in COST_MODEL_VERSIONS:
        raise BookError(f"{sid}: unknown cost_model_version {raw['cost_model_version']!r}")
    if raw["requested_stage"] not in STAGES:
        raise BookError(f"{sid}: unknown requested_stage {raw['requested_stage']!r}")
    band = raw.get("band")
    if execution in BANDED_EXECUTIONS:
        if not _fraction(band, upper_inclusive=False):
            raise BookError(f"{sid}: {execution} execution requires band in [0, 1)")
        band = float(band)
    elif band is not None:
        raise BookError(f"{sid}: band applies only to continuous executions")
    sleeves, limits = _sleeves_and_limits(sid, raw, execution, source)
    nav = raw["shadow_nav_krw"]
    if isinstance(nav, bool) or not isinstance(nav, int) or nav <= 0:
        raise BookError(f"{sid}: shadow_nav_krw must be a positive integer")
    user = raw["account_user_id"]
    if user is not None:
        user = _text(user, "account_user_id", sid)
    prereg = raw.get("preregistration")
    if prereg is not None:
        prereg = _text(prereg, "preregistration", sid)
    early = raw.get("early_paper")
    if early is not None:
        if not isinstance(early, dict) or set(early) != _EARLY_PAPER_KEYS:
            raise BookError(f"{sid}: early_paper must have exactly {sorted(_EARLY_PAPER_KEYS)}")
        if raw["requested_stage"] != "paper":
            raise BookError(f"{sid}: early_paper requires requested_stage paper")
        decided_at = _text(early["decided_at"], "early_paper.decided_at", sid)
        try:
            stamp = datetime.fromisoformat(decided_at)
        except ValueError as error:
            raise BookError(f"{sid}: early_paper.decided_at is not ISO 8601") from error
        if stamp.tzinfo is None:
            raise BookError(f"{sid}: early_paper.decided_at needs a timezone")
        early = {"decided_by": _text(early["decided_by"], "early_paper.decided_by", sid),
                 "decided_at": stamp.isoformat(), "reason": _text(early["reason"], "early_paper.reason", sid)}
    holdout_ids = _names(holdout["experiment_ids"], "holdout.experiment_ids", sid, allow_empty=early is not None)
    holdout_variants = _names(holdout["variants"], "holdout.variants", sid, allow_empty=early is not None)
    if bool(holdout_ids) != bool(holdout_variants):
        raise BookError(f"{sid}: holdout experiment_ids and variants must both be empty or both nonempty")
    return StrategyEntry(
        strategy_id=sid, title=_text(raw["title"], "title", sid),
        description=_text(raw["description"], "description", sid),
        experiment_id=_text(raw["experiment_id"], "experiment_id", sid),
        variant=_text(raw["variant"], "variant", sid),
        holdout_experiment_ids=holdout_ids, holdout_variants=holdout_variants,
        decision_kind=source["kind"], decision_rule=source["rule"], execution=execution,
        cost_model_version=raw["cost_model_version"], band=band, requested_stage=raw["requested_stage"],
        shadow_nav_krw=nav, account_user_id=user, preregistration=prereg, early_paper=early,
        sleeves=sleeves, limits=limits, t0=_t0(sid, raw, execution))


def load_book(path: str | Path) -> tuple[StrategyEntry, ...]:
    raw = _load_yaml(Path(path))
    if not isinstance(raw, dict) or set(raw) != {"strategies"}:
        raise BookError("strategy book must contain exactly one key: strategies")
    strategies = raw["strategies"]
    if not isinstance(strategies, dict) or not strategies:
        raise BookError("strategies must be a nonempty mapping")
    entries = tuple(_entry(sid, value) for sid, value in strategies.items())
    users = [e.account_user_id for e in entries if e.account_user_id is not None]
    if len(set(users)) != len(users):
        raise BookError("one account user can serve only one strategy")
    return entries


@dataclass(frozen=True)
class Approval:
    strategy_id: str
    stage: str
    approved_by: str
    approved_at: datetime
    evidence: str


def _aware(value, index) -> datetime:
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, str):
        try:
            stamp = datetime.fromisoformat(value)
        except ValueError:
            raise BookError(f"approval {index}: approved_at is not ISO 8601") from None
    elif isinstance(value, date):
        raise BookError(f"approval {index}: approved_at needs a time and timezone")
    else:
        raise BookError(f"approval {index}: approved_at must be an ISO 8601 string")
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise BookError(f"approval {index}: approved_at must include a timezone")
    return stamp


def load_approvals(path: str | Path) -> tuple[Approval, ...]:
    raw = _load_yaml(Path(path))
    if not isinstance(raw, dict) or set(raw) != {"approvals"}:
        raise BookError("approvals file must contain exactly one key: approvals")
    items = raw["approvals"]
    if not isinstance(items, list):
        raise BookError("approvals must be a list")
    approvals = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or set(item) != _APPROVAL_KEYS:
            raise BookError(f"approval {index}: keys must be exactly {sorted(_APPROVAL_KEYS)}")
        if item["stage"] not in APPROVAL_STAGES:
            raise BookError(f"approval {index}: unknown stage {item['stage']!r}")
        sid = item["strategy_id"]
        if not isinstance(sid, str) or not STRATEGY_ID.match(sid):
            raise BookError(f"approval {index}: invalid strategy_id")
        approvals.append(Approval(sid, item["stage"], _text(item["approved_by"], "approved_by", sid),
                                  _aware(item["approved_at"], index), _text(item["evidence"], "evidence", sid)))
    return tuple(approvals)
