"""Read-only DART survivorship/date diagnostic; only --execute publishes reports.

No original financial values can be recovered by this diagnostic. Selection
contexts are descriptive null comparisons, not independent causal tests (the
same stock can occur at many decisions). Frozen LH002 files are never changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import resource
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.stats import binomtest, hypergeom

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backtesting.experiments.lh001 import arms, inputs
from src.ingestion.dart_filing_dates import annotate_versions, provenance_index
from src.ingestion.dart_list_supplement import DEFAULT_DATA_DIR, targets
from src.ingestion.dart_listing import directory, file_hash, listing_status, listing_version
from src.ingestion.storage import read_rows


class _JSONStream:
    """Bounded stdlib decoder: skip observation arrays, retain one bundle only."""
    def __init__(self, handle):
        self.handle, self.buffer, self.pos, self.eof = handle, "", 0, False
        self.decoder, self.sha256 = json.JSONDecoder(), hashlib.sha256()

    def more(self):
        chunk = self.handle.read(1024 * 1024)
        self.sha256.update(chunk.encode("utf-8"))
        self.buffer, self.pos = self.buffer[self.pos:] + chunk, 0
        self.eof = not chunk

    def peek(self):
        while True:
            while self.pos < len(self.buffer) and self.buffer[self.pos].isspace():
                self.pos += 1
            if self.pos < len(self.buffer):
                return self.buffer[self.pos]
            if self.eof:
                return ""
            self.more()

    def take(self, token):
        if self.peek() != token:
            raise ValueError("LH002 snapshot invalid JSON structure")
        self.pos += 1

    def value(self):
        self.peek()
        while True:
            try:
                value, end = self.decoder.raw_decode(self.buffer, self.pos)
                if end == len(self.buffer) and not self.eof:
                    self.more()
                    continue
                self.pos = end
                return value
            except json.JSONDecodeError:
                if self.eof:
                    raise ValueError("LH002 snapshot invalid or truncated JSON") from None
                self.more()

    def array(self):
        self.take("[")
        if self.peek() != "]":
            while True:
                yield self.value()
                if self.peek() == "]":
                    break
                self.take(",")
        self.take("]")

    def skip(self):
        if self.peek() == "[":
            for value in self.array():
                del value
        else:
            self.value()

    def prepared(self, prefix=()):
        self.take("{")
        if self.peek() != "}":
            while True:
                key = self.value()
                if not isinstance(key, str):
                    raise ValueError("LH002 snapshot object key must be a string")
                self.take(":")
                path = prefix + (key,)
                if path == ("data", "prepared"):
                    yield from self.array()
                elif path == ("data",):
                    yield from self.prepared(path)
                else:
                    self.skip()
                if self.peek() == "}":
                    break
                self.take(",")
        self.take("}")


def _distribution(values):
    if not values:
        return {"count": 0, "min": None, "p25": None, "median": None, "p75": None, "p95": None, "max": None}
    return {"count": len(values), **dict(zip(("min", "p25", "median", "p75", "p95", "max"),
                map(float, np.percentile(values, (0, 25, 50, 75, 95, 100)))))}


def quarterly_impact(data_dir):
    index = provenance_index(data_dir)
    years, correction_lags, linked_lags = {}, [], []
    for path in sorted((Path(data_dir) / "fundamentals/dart_quarterly").glob("[0-9][0-9][0-9][0-9]_110*.jsonl")):
        year = path.stem[:4]
        group = years.setdefault(year, {"counts": Counter(), "lags": []})
        for row in annotate_versions(read_rows(path), index=index):
            if str(row["bsns_year"]) != year:
                raise ValueError("DART quarterly business year disagrees with archive")
            counts = group["counts"]
            counts["versions"] += 1
            linked = row["list_rcept_dt"] is not None
            kind = "corrections" if row["is_correction"] else "originals" if linked else "unlinked"
            counts[kind] += 1
            if linked:
                lag = (datetime.fromisoformat(row["known_date_stored"]) - datetime.fromisoformat(row["known_date_original"])).days
                linked_lags.append(lag)
                counts["list_date_differs_from_receipt_prefix"] += row["list_rcept_dt"] != row["rcept_no"][:8]
                if row["is_correction"]:
                    if row["original_rcept_no"] is not None:
                        counts["corrections_with_original"] += 1
                        correction_lags.append(lag)
                        group["lags"].append(lag)
                    else:
                        counts["corrections_without_original"] += 1
    by_year = {}
    for year, group in years.items():
        counts = {name: group["counts"][name] for name in ("versions", "corrections", "originals", "unlinked",
                 "corrections_with_original", "corrections_without_original", "list_date_differs_from_receipt_prefix")}
        by_year[year] = {**counts, "shares": {k: counts[k] / counts["versions"] if counts["versions"] else None
                         for k in ("corrections", "originals", "unlinked")},
                         "correction_delay_days": _distribution(group["lags"])}
    return {"year_basis": "bsns_year; each saved fs_div version counts once", "by_year": by_year,
            "correction_delay_days": _distribution(correction_lags), "all_linked_delay_days": _distribution(linked_lags),
            "limitation": "Missing listings cannot establish correction/original identity; no original values recovered."}


def supplement_impact(data_dir):
    folder = directory(data_dir) / "list_supplement"
    paths = sorted(folder.glob("*/*.jsonl"))
    if not folder.exists():
        return {"available": False, "by_year": None, "note": "Supplement absent: missed filing/event counts cannot yet be measured."}
    result = {}
    for path in paths:
        group = result.setdefault(path.stem[:4], {"filings": 0, "categories": dict.fromkeys(inputs.CATEGORIES, 0),
                           "non_bonus_capital_raise_decisions": 0, "hf001_hc003_original_rights_events": 0})
        for row in read_rows(path):
            if row["rcept_dt"] != path.stem:
                raise ValueError("DART supplement filing date disagrees with archive")
            title = row["report_nm"]
            group["filings"] += 1
            group["categories"][inputs._category({"title": title, "source_type": "dart"})] += 1
            raise_event = "유상증자결정" in title and "무상" not in title
            group["non_bonus_capital_raise_decisions"] += raise_event
            group["hf001_hc003_original_rights_events"] += raise_event and not any(w in title for w in ("정정", "철회")) and bool(row["stock_code"])
    return {"available": True, "by_year": result,
            "note": "Published supplement rows only; partial collection is not a total missing-filings estimate."}


def _exposure(stocks, codes, affected):
    selected = [stocks[c] for c in codes]
    impacted = [row for row in selected if row["stock_code"] in affected]
    def zero(row):
        return all(row.get("disclosures_" + category) == 0 for category in inputs.CATEGORIES)
    return {"candidates": len(selected), "affected_candidates": len(impacted),
            "affected_share": len(impacted) / len(selected) if selected else None,
            "all_zero_candidates": sum(zero(row) for row in selected),
            "affected_all_zero": sum(zero(row) for row in impacted),
            "affected_counts_unavailable": sum(any(row.get("disclosures_" + c) is None for c in inputs.CATEGORIES) for row in impacted)}


def _selection_context(codes, population, affected):
    if len(codes) != len(set(codes)) or not set(codes).issubset(population):
        raise ValueError("LH002 cached selection contains duplicate or unshown candidates")
    total, exposed, n, k = len(population), len(set(population) & affected), len(codes), len(set(codes) & affected)
    p = exposed / total if total else None
    return {"count": n, "affected": k, "affected_share": k / n if n else None,
            "expected_affected": n * p if p is not None else None,
            "binomial_two_sided_p": float(binomtest(k, n, p).pvalue) if n and p is not None else None,
            "hypergeometric_p_ge": float(hypergeom.sf(k - 1, total, exposed, n)) if n and total else None,
            "hypergeometric_p_le": float(hypergeom.cdf(k, total, exposed, n)) if n and total else None}


def lh002_impact(data_dir, affected):
    root = Path(data_dir) / "research/lh002"
    snapshot = root / "screening_snapshot.json"
    if not snapshot.is_file():
        return {"available": False, "reason": "screening_snapshot_absent"}
    cached = root / "screening.json"
    decisions = json.loads(cached.read_text(encoding="utf-8"))["decisions"] if cached.is_file() else []
    by_day = {row["decision_date"]: row for row in decisions}
    if len(by_day) != len(decisions):
        raise ValueError("LH002 duplicate cached decisions")
    per_decision, totals = [], {a: Counter() for a in ("b_num", "c_num")}
    # Open the ~650 MB snapshot exactly once. Observations are decoded and
    # discarded individually; only one prepared bundle is resident at a time.
    with snapshot.open(encoding="utf-8", newline="") as handle:
        stream = _JSONStream(handle)
        for prepared in stream.prepared():
            bundle = prepared["bundle"]
            day = bundle["decision_date"]
            stocks = {row["stock_code"]: row for row in bundle["stocks"]}
            if len(stocks) != len(bundle["stocks"]):
                raise ValueError("LH002 duplicate snapshot candidate codes")
            recorded = by_day.get(day)
            base = {"decision_date": day, "universe": _exposure(stocks, list(stocks), affected), "arms": {}}
            mapping = {identity: code for code, identity in inputs.id_map(bundle, True).items()}
            for arm in ("b_num", "c_num"):
                aggregate = recorded["arms"].get(arm) if recorded else None
                if aggregate is None:
                    base["arms"][arm] = {"available": False, "reason": "cached_decision_absent"}
                    totals[arm]["missing_decisions"] += 1
                    continue
                if arm == "b_num":
                    sectors = {row["industry"] for row in bundle["industries"]
                               if row["id"] in recorded["arms"].get("b_stage1", {}).get("selected", [])}
                    pool = arms.cap_codes(bundle, [c for c, row in stocks.items() if row["industry"] in sectors])
                else:
                    pool = arms.cap_codes(bundle, bundle["a_codes"])
                exposure = _exposure(stocks, pool, affected)
                calls, valid = [], 0
                for key in aggregate.get("calls", []):
                    if not isinstance(key, str) or len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
                        raise ValueError("LH002 invalid cached call key")
                    path = root / "calls" / f"{key}.json"
                    if not path.is_file():
                        calls.append({"key": key, "available": False})
                        totals[arm]["missing_calls"] += 1
                        continue
                    call = json.loads(path.read_text(encoding="utf-8"))
                    if call.get("key") != key or call.get("stage") != "screening":
                        raise ValueError("LH002 cached call identity/stage mismatch")
                    if not call.get("valid"):
                        calls.append({"key": key, "available": True, "valid": False})
                        totals[arm]["invalid_calls"] += 1
                        continue
                    output = call["output"]
                    contexts = {}
                    for kind in ("ranked", "avoid"):
                        codes = [mapping[row["id"]] for row in output.get(kind, [])]
                        context = _selection_context(codes, pool, affected)
                        contexts[kind] = context
                        totals[arm][kind + "_slots"] += context["count"]
                        totals[arm][kind + "_affected"] += context["affected"]
                        totals[arm][kind + "_expected"] += context["expected_affected"] or 0
                    calls.append({"key": key, "available": True, "valid": True, **contexts})
                    valid += 1
                base["arms"][arm] = {"available": True, **exposure, "calls": calls,
                    "aggregate_ranked": _selection_context(aggregate.get("candidates", []), pool, affected),
                    "aggregate_selected": _selection_context(aggregate.get("selected", []), pool, affected),
                    "aggregate_avoid": _selection_context(aggregate.get("avoid", []), pool, affected)}
                for name in ("candidates", "affected_candidates", "all_zero_candidates", "affected_all_zero", "affected_counts_unavailable"):
                    totals[arm][name] += exposure[name]
                totals[arm]["decisions"] += 1
                totals[arm]["valid_calls"] += valid
            per_decision.append(base)
        if stream.peek():
            raise ValueError("LH002 snapshot has trailing JSON content")
        snapshot_sha = stream.sha256.hexdigest()
    if not per_decision:
        raise ValueError("LH002 snapshot contains no prepared decisions")
    summary = {}
    for arm, counter in totals.items():
        group = dict(counter)
        group["candidate_affected_share"] = counter["affected_candidates"] / counter["candidates"] if counter["candidates"] else None
        for kind in ("ranked", "avoid"):
            n, k, expected = (counter[kind + suffix] for suffix in ("_slots", "_affected", "_expected"))
            p = expected / n if n else None
            group[kind] = {"count": n, "affected": k, "affected_share": k / n if n else None,
                           "expected_affected": expected if n else None, "candidate_share_call_weighted": p,
                           "pooled_binomial_two_sided_p": float(binomtest(k, n, p).pvalue) if n else None}
        summary[arm] = group
    return {"available": True, "snapshot_sha256": snapshot_sha, "cached_decisions_sha256": file_hash(cached),
            "decision_count": len(per_decision), "per_decision": per_decision, "by_arm": summary,
            "context": "Per-call hypergeometric sampling without replacement; pooled binomial is approximate, repeated decisions/candidates are dependent. B uses its sector-capped shown pool, not its full-universe D-control."}


def diagnose(*, data_dir=DEFAULT_DATA_DIR):
    started = time.perf_counter()
    selection = targets(data_dir)
    affected = set(selection["affected_stock_codes"])
    report = {"status": "dry_run", "data_dir": str(data_dir), "listing_version": listing_version(data_dir),
              "listing_status": listing_status(data_dir), "target_corps": len(selection["corps"]),
              "affected_stock_codes": len(affected), "current_class_counts": selection["current_class_counts"],
              "unmapped_stock_days": selection["unmapped_stock_days"], "krx_by_year": selection["by_year"],
              "supplement": supplement_impact(data_dir), "quarterly": quarterly_impact(data_dir),
              "lh002": lh002_impact(data_dir, affected)}
    report["performance"] = {"runtime_seconds": time.perf_counter() - started,
                             "peak_rss_mib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}
    return report


def korean_summary(report):
    lines = ["# DART 공시 목록 생존 편향 및 재무 날짜 진단", "",
             f"- 보완 대상: {report['target_corps']}개 회사, {report['affected_stock_codes']}개 종목 코드",
             f"- 보완 완료: {report['listing_status']['supplement_complete']}; 범위 끝: {report['listing_status']['end_de']}",
             "- 보완 목록이 없으면 누락 공시 수는 측정 불가이며, 0건으로 해석하지 않습니다.", "",
             "| 연도 | 영향 회사 | 영향 종목 | 영향 stock-days | 전체 대비 |",
             "| --- | ---: | ---: | ---: | ---: |"]
    for year, row in report["krx_by_year"].items():
        lines.append(f"| {year} | {row['affected_corps']} | {row['affected_stock_codes']} | {row['affected_stock_days']} | {row['affected_share']:.4%} |")
    lines += ["", "저장 재무의 최초 수치는 복원하지 않았습니다. 접수번호와 목록의 늦은 날짜를 known date로 삼아 정정 지연을 비교합니다.",
              "목록 미연결은 정정 여부 미상으로 분리합니다. 원천 파일·스냅샷·캐시·기존 결과·대장은 변경하지 않았습니다.", "",
              "| 재무연도 | 저장 버전 | 정정 | 최초본 | 목록 미연결 | 정정 지연 중앙값(일) |",
              "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for year, row in report["quarterly"]["by_year"].items():
        lines.append(f"| {year} | {row['versions']} | {row['corrections']} | {row['originals']} | {row['unlinked']} | {row['correction_delay_days']['median']} |")
    if report["supplement"]["by_year"] is not None:
        lines += ["", "| 보완 공시 연도 | 공시 수 | 비무상 유상증자결정 | HF001/HC003 최초 이벤트 |", "| --- | ---: | ---: | ---: |"]
        for year, row in report["supplement"]["by_year"].items():
            lines.append(f"| {year} | {row['filings']} | {row['non_bonus_capital_raise_decisions']} | {row['hf001_hc003_original_rights_events']} |")
    if report["lh002"]["available"]:
        lines += ["", f"LH002 스냅샷 {report['lh002']['decision_count']}개 결정의 실제 제시 후보를 분석했습니다."]
        for arm, row in report["lh002"]["by_arm"].items():
            lines.append(f"- {arm}: 후보 {row.get('candidates', 0)}행 중 영향 {row.get('affected_candidates', 0)}행; 영향 후보의 공시 건수 전부 0: {row.get('affected_all_zero', 0)}행. ranked={row['ranked']}; avoid={row['avoid']}")
        lines.append("각 호출의 이항·초기하 비교는 단순 무작위 선택 맥락입니다. 반복 결정·종목의 의존성을 보정한 인과 검정이 아닙니다. B의 후보는 업종 선정 후 제시된 실제 풀입니다.")
    else:
        lines += ["", "LH002 스냅샷 없음: 선택 노출 측정 불가."]
    lines += ["", f"- runtime: {report['performance']['runtime_seconds']:.2f}s; peak RSS: {report['performance']['peak_rss_mib']:.2f} MiB", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--execute", action="store_true", help="Write diagnostic JSON and Korean Markdown; no API/model calls")
    args = parser.parse_args(argv)
    report = diagnose(data_dir=args.data_dir)
    if args.execute:
        report["status"] = "published"
        output = Path(__file__).resolve().parents[2] / "research/experiments/data_issues"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        stem = output / ("dart_list_survivorship_" + stamp)
        encoded = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        output.mkdir(parents=True, exist_ok=True)
        # Reserve both names before writing; even a timestamp collision must
        # fail instead of replacing a diagnostic already published.
        with stem.with_suffix(".json").open("x", encoding="utf-8") as json_file:
            with stem.with_suffix(".md").open("x", encoding="utf-8") as summary_file:
                json_file.write(encoded)
                summary_file.write(korean_summary(report))
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
