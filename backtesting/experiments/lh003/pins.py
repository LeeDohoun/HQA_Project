"""One code pin contract, with audited, data-only statutory/calendar amendments."""
import ast
import hashlib
import math
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from backtesting import cost_model
from src.runner import trading_calendar

from . import Refused
from .storage import aware, digest, file_hash, now, seal, verified

MECHANICAL = {"backtesting/cost_model.py": cost_model, "src/runner/trading_calendar.py": trading_calendar}
_LOADED_HASHES = {name: file_hash(module.__file__) for name, module in MECHANICAL.items()}


def source_hash(source):
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def loaded_hashes():
    # Updating a file after import does not update its loaded code/tables. Apply
    # amendments before launching a fresh process, rather than certifying stale
    # runtime objects with the hash of newly written source.
    return dict(_LOADED_HASHES)


def _date(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise Refused("mechanical data requires an ISO date")
    return date.fromisoformat(value)


def _url(value):
    if not isinstance(value, str) or urlparse(value).scheme not in ("https", "http") or not urlparse(value).netloc:
        raise Refused("amendment requires a legal/official source URL")


def _literal(node):
    for item in ast.walk(node):
        if isinstance(item, ast.Dict):
            keys = [ast.literal_eval(k) for k in item.keys]
            if len(set(keys)) != len(keys):
                raise Refused("duplicate mechanical data keys")
    return ast.literal_eval(node)


def structure(source, path):
    """Validate exact data shapes, then mask only the named data AST nodes."""
    try:
        tree = ast.parse(source)
        assignments = {n.targets[0].id: n for n in tree.body if isinstance(n, ast.Assign)
                       and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)}
        if path == "backtesting/cost_model.py":
            node = assignments["SELL_TAX"]
            if not isinstance(node.value, ast.Tuple) or not node.value.elts:
                raise Refused("SELL_TAX must be a nonempty tuple")
            dates = []
            for item in node.value.elts:
                if not isinstance(item, ast.Tuple) or len(item.elts) != 2:
                    raise Refused("invalid SELL_TAX row")
                call, rates = item.elts
                if (not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name) or call.func.id != "date"
                        or call.keywords or len(call.args) != 3
                        or any(not isinstance(v, ast.Constant) or type(v.value) is not int for v in call.args)):
                    raise Refused("SELL_TAX dates must be literal date(year, month, day)")
                dates.append(date(*(v.value for v in call.args)))
                values = _literal(rates)
                if (not isinstance(values, dict) or set(values) != {"KOSPI", "KOSDAQ"}
                        or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v < 1 for v in values.values())):
                    raise Refused("invalid SELL_TAX market/rate literals")
            if dates != sorted(set(dates)):
                raise Refused("SELL_TAX dates must be strictly increasing")
            node.value = ast.Constant(value="<SELL_TAX>")
        elif path == "src/runner/trading_calendar.py":
            values = {key: _literal(assignments[key].value) for key in
                      ("CALENDAR_VERIFIED_THROUGH", "KRX_ADDITIONAL_CLOSED_DAYS", "PENDING_SPECIAL_SESSIONS", "SPECIAL_CLOSES")}
            through = _date(values["CALENDAR_VERIFIED_THROUGH"])
            for key in ("KRX_ADDITIONAL_CLOSED_DAYS", "PENDING_SPECIAL_SESSIONS", "SPECIAL_CLOSES"):
                if not isinstance(values[key], dict):
                    raise Refused("calendar tables must be literal dictionaries")
                for day, notice in values[key].items():
                    _date(day)
                    if key == "PENDING_SPECIAL_SESSIONS":
                        if not isinstance(notice, str) or not notice.strip():
                            raise Refused("pending calendar notice must be text")
                    elif key == "KRX_ADDITIONAL_CLOSED_DAYS":
                        if not isinstance(notice, dict) or set(notice) != {"reason", "source"} or not isinstance(notice["reason"], str):
                            raise Refused("invalid calendar closure notice")
                        _url(notice["source"])
                    else:
                        if not isinstance(notice, dict) or set(notice) != {"close", "published_at", "source_urls"}:
                            raise Refused("invalid special-close notice literals")
                        if aware(notice["close"]).date().isoformat() != day:
                            raise Refused("special close differs from its date")
                        aware(notice["published_at"])
                        if not isinstance(notice["source_urls"], dict) or set(notice["source_urls"]) != {"KOSPI", "KOSDAQ"}:
                            raise Refused("special-close notice requires both markets")
                        for url in notice["source_urls"].values():
                            _url(url)
                assignments[key].value = ast.Constant(value="<" + key + ">")
            assignments["CALENDAR_VERIFIED_THROUGH"].value = ast.Constant(value="<verified-through>")
            version = assignments["CALENDAR_VERSION"].value
            if (not isinstance(version, ast.BinOp) or not isinstance(version.op, ast.Add)
                    or not isinstance(version.right, ast.Constant) or not isinstance(version.right.value, str)
                    or not version.right.value.startswith(":XKRX:krx-notices-through-")):
                raise Refused("invalid calendar version data suffix")
            version.right = ast.Constant(value="<notice-version>")
            function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_calendar")
            bounds = [n for n in ast.walk(function) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "min"]
            if (len(bounds) != 1 or bounds[0].keywords or len(bounds[0].args) != 2
                    or not isinstance(bounds[0].args[0], ast.Constant) or type(bounds[0].args[0].value) is not int
                    or bounds[0].args[0].value != through.year
                    or ast.dump(bounds[0].args[1]) != ast.dump(ast.parse("year + 1", mode="eval").body)):
                raise Refused("calendar _calendar min boundary must match the verified-through year")
            bounds[0].args[0] = ast.Constant(value="<verified-year-boundary>")
        else:
            raise Refused("file is outside the mechanical amendment allowlist")
        return ast.dump(tree, include_attributes=False)
    except (ValueError, TypeError, KeyError, StopIteration, SyntaxError) as exc:
        raise Refused("invalid mechanical data AST: " + str(exc)) from exc


def transition(before, after, path):
    if structure(before, path) != structure(after, path):
        raise Refused("pinned implementation AST changed outside permitted data nodes: " + path)
    if ast.dump(ast.parse(before)) == ast.dump(ast.parse(after)):
        raise Refused("amendment must change statutory/calendar data")


def capture(root, repo_root):
    sources = {name: (Path(repo_root) / name).read_text(encoding="utf-8") for name in MECHANICAL}
    for name, source in sources.items():
        structure(source, name)
    seal(Path(root) / "code_baseline.json", sources)
    return digest(sources)


def _chain(root, start, *, at, baseline=None):
    sources = verified(Path(root) / "code_baseline.json") if baseline is None else dict(baseline)
    if digest(sources) != start["code_baseline_sha256"] or set(sources) != set(MECHANICAL):
        raise Refused("pinned implementation baseline differs from start")
    hashes = dict(start["implementation_hashes"])
    if any(source_hash(sources[n]) != hashes[n] for n in MECHANICAL):
        raise Refused("pinned implementation source differs from baseline hashes")
    paths = sorted((Path(root) / "amendments").glob("*.json")) if root is not None else []
    if [p.stem for p in paths] != [f"{i:06d}" for i in range(len(paths))]:
        raise Refused("amendment journal is not contiguous")
    applied, previous_time = [], aware(start["at"])
    for path in paths:
        record = verified(path)
        name = record["path"]
        _url(record["legal_source"])
        _date(record["effective_date"])
        changed = aware(record["changed_at"])
        if name not in MECHANICAL or changed < previous_time:
            raise Refused("invalid amendment path or change-time order")
        if record["before_sha256"] != source_hash(sources[name]) or record["after_sha256"] != source_hash(record["after_source"]):
            raise Refused("amendment before/after hash chain differs")
        transition(sources[name], record["after_source"], name)
        sources[name] = record["after_source"]
        previous_time = changed
        if changed <= aware(at):
            hashes[name] = record["after_sha256"]
            applied.append(digest(record))
    return hashes, applied, sources


def verify(root, repo_root, *, at, current, historical=False, reference=None):
    start = verified(Path(root) / "start.json") if reference is None else reference["start"]
    expected, amendments, _ = _chain(root, start, at=at,
                                    baseline=None if reference is None else reference["sources"])
    if not historical and (current != expected or any(loaded_hashes()[n] != current[n] for n in MECHANICAL)):
        raise Refused("pinned implementation hashes differ from loaded code or approved amendments")
    return {"implementation_hashes": expected, "amendment_hashes": amendments,
            "code_baseline_sha256": start["code_baseline_sha256"]}


def record_amendment(root, repo_root, *, path, legal_source, effective_date, changed_at, clock=now):
    """Record a reviewed data-only update; this function never edits either module."""
    start = verified(Path(root) / "start.json")
    if aware(changed_at) > aware(clock()):
        raise Refused("amendment change time is in the future")
    _url(legal_source)
    _date(effective_date)
    hashes, _, sources = _chain(root, start, at=clock())
    if path not in MECHANICAL:
        raise Refused("file is outside the mechanical amendment allowlist")
    after = (Path(repo_root) / path).read_text(encoding="utf-8")
    transition(sources[path], after, path)
    record = {"path": path, "legal_source": legal_source, "effective_date": effective_date,
              "changed_at": aware(changed_at).isoformat(), "before_sha256": hashes[path],
              "after_sha256": source_hash(after), "after_source": after}
    records = sorted((Path(root) / "amendments").glob("*.json"))
    previous = verified(records[-1])["changed_at"] if records else start["at"]
    if aware(changed_at) < aware(previous):
        raise Refused("amendment change time precedes its baseline or previous amendment")
    seal(Path(root) / "amendments" / f"{len(records):06d}.json", record)
    return record
