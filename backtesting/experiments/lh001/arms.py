"""Fixed B/C candidate pools, output validation, and repeat aggregation."""
from __future__ import annotations

from itertools import combinations

from . import inputs
from .config import LH001


def validate_selection(output, shown_ids, *, count, avoid=0):
    selected = [row["id"] for row in output["ranked"]]
    avoided = [row["id"] for row in output.get("avoid", [])]
    if len(selected) != count or len(avoided) != avoid:
        raise ValueError("wrong ranked/avoid list length")
    all_ids = selected + avoided
    if len(set(all_ids)) != len(all_ids):
        raise ValueError("duplicate candidate IDs")
    if not set(all_ids).issubset(shown_ids):
        raise ValueError("unknown candidate IDs")


def aggregate(rankings, size, *, eligible=None):
    """Selection frequency, then mean rank among appearances, then lexical ID."""
    ranks = {}
    for ranking in rankings:
        if len(set(ranking)) != len(ranking):
            raise ValueError("duplicate IDs in aggregation")
        for rank, identity in enumerate(ranking, 1):
            if eligible is None or identity in eligible:
                ranks.setdefault(identity, []).append(rank)
    ordered = sorted(ranks, key=lambda identity: (-len(ranks[identity]), sum(ranks[identity]) / len(ranks[identity]), identity))
    return ordered[:size] if len(ordered) >= size else []


def jaccard(rankings):
    values = []
    for left, right in combinations(rankings, 2):
        a, b = set(left), set(right)
        if a | b:
            values.append(len(a & b) / len(a | b))
    return sum(values) / len(values) if values else None


def cap_codes(bundle, codes=None):
    allowed = {row["stock_code"] for row in bundle["stocks"]} if codes is None else set(codes)
    rows = sorted((row for row in bundle["stocks"] if row["stock_code"] in allowed),
                  key=lambda row: (-row["adv_20"], row["stock_code"]))
    return [row["stock_code"] for row in rows[:300]]


def _empty(reason, pool=None):
    return {"selected": [], "candidates": [], "avoid": [], "random_pool": pool or [],
            "jaccard": None, "invalid_calls": 0, "invalid_by_call": {}, "text_missing": 0, "calls": [], "reason": reason}


def _repeated(runner, bundle, arm, codes, *, repeats, anonymised, text_loader=None, stage, config=LH001):
    is_text, is_c = arm.endswith("text"), arm.startswith("c")
    required = 10 if is_text else 30
    avoid = 10 if is_c and not is_text else 0
    if len(codes) < required + avoid:
        return _empty("insufficient_candidates", list(codes))
    texts = inputs.business_texts(codes, bundle["decision_date"], runner.directory.parents[1], loader=text_loader, config=config) if is_text else None
    rendered, mapping = inputs.render(bundle, anonymised=anonymised, codes=codes, texts=texts, final_window=stage == "final")
    template = f"v1_{'c' if is_c else 'b'}_{'text' if is_text else 'num'}.txt"
    schema = f"v1_{'c_num' if avoid else 'text' if is_text else 'b_num'}.schema.json"
    calls = [runner.call(template, rendered, schema, index, stage=stage,
                         validate=lambda output: validate_selection(output, set(mapping), count=required, avoid=avoid))
             for index in range(repeats)]
    invalid = sum(attempt["state"] == "invalid" for call in calls for attempt in call["attempts"])
    base = {"calls": [call["key"] for call in calls], "invalid_calls": invalid,
            "invalid_by_call": {call["key"]: sum(attempt["state"] == "invalid" for attempt in call["attempts"]) for call in calls},
            "text_missing": sum(row["missing"] for row in texts.values()) if texts is not None else 0,
            "text_provenance": {code: row["provenance"] for code, row in texts.items()} if texts is not None else {},
            "random_pool": list(codes)}
    if not all(call["valid"] for call in calls):
        return {**_empty("invalid_call", list(codes)), **base}
    rankings = [[row["id"] for row in call["output"]["ranked"]] for call in calls]
    candidates = aggregate(rankings, required)
    buys = aggregate([ranking[:10] for ranking in rankings], 10, eligible=set(candidates))
    avoids = aggregate([[row["id"] for row in call["output"]["avoid"]] for call in calls], 10,
                       eligible=set(mapping) - set(buys)) if avoid else []
    return {**base, "selected": [mapping[identity] for identity in buys],
            "candidates": [mapping[identity] for identity in candidates], "avoid": [mapping[identity] for identity in avoids],
            "jaccard": jaccard([ranking[:10] for ranking in rankings]), "reason": None,
            "repeat_selections": [[mapping[identity] for identity in ranking[:10]] for ranking in rankings]}


def run_decision(runner, bundle, *, final, anonymised=None, text_loader=None, stage=None, c_only=False, config=LH001):
    repeats = 3 if final else 1
    anonymised = not final if anonymised is None else anonymised
    stage = stage or ("final" if final else "screening")
    results = {}
    if not c_only:
        rendered, _ = inputs.render(bundle, anonymised=anonymised, industry_only=True)
        groups = {row["id"]: row["industry"] for row in bundle["industries"]}
        calls = [runner.call("v1_b_stage1.txt", rendered, "v1_b_stage1.schema.json", index, stage=stage,
                             validate=lambda output: validate_selection(output, set(groups), count=3)) for index in range(repeats)]
        if all(call["valid"] for call in calls):
            selected = aggregate([[row["id"] for row in call["output"]["ranked"]] for call in calls], 3)
            sectors = {groups[identity] for identity in selected}
            pool = cap_codes(bundle, [row["stock_code"] for row in bundle["stocks"] if row["industry"] in sectors])
            results["b_num"] = _repeated(runner, bundle, "b_num", pool, repeats=repeats, anonymised=anonymised, stage=stage, config=config)
        else:
            selected = []
            results["b_num"] = _empty("invalid_stage1")
        # Preregistered B D-control is the capped full universe, not the LLM sectors.
        results["b_num"]["random_pool"] = cap_codes(bundle)
        results["b_stage1"] = {"selected": selected, "calls": [call["key"] for call in calls],
                               "invalid_by_call": {call["key"]: sum(attempt["state"] == "invalid" for attempt in call["attempts"]) for call in calls},
                               "jaccard": jaccard([[row["id"] for row in call["output"]["ranked"]] for call in calls if call["valid"]]),
                               "invalid_calls": sum(attempt["state"] == "invalid" for call in calls for attempt in call["attempts"])}
        if final:
            candidates = results["b_num"]["candidates"]
            results["b_text"] = _repeated(runner, bundle, "b_text", candidates, repeats=repeats, anonymised=False,
                                           text_loader=text_loader, stage=stage, config=config) if len(candidates) == 30 else _empty("no_numeric_candidates")
    c_pool = cap_codes(bundle, bundle["a_codes"])
    results["c_num"] = _repeated(runner, bundle, "c_num", c_pool, repeats=repeats, anonymised=anonymised, stage=stage, config=config)
    if final:
        candidates = results["c_num"]["candidates"]
        results["c_text"] = _repeated(runner, bundle, "c_text", candidates, repeats=repeats, anonymised=False,
                                      text_loader=text_loader, stage=stage, config=config) if len(candidates) == 30 else _empty("no_numeric_candidates")
    return results
