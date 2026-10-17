"""Offline LH002 screening post-hoc diagnostics; only --execute writes reports.

Never invokes a runner, collector, holdout session, model, network or registry.
All performance is reconstructed from the one-pass frozen screening snapshot.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import resource
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.runner import trading_calendar
from backtesting.experiments.lh001 import arms, inputs
from scripts.research.dart_list_survivorship_impact import _JSONStream

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = Path('research/experiments/LH002_llm_hegemony_judge')
RESULT = EXPERIMENT / 'results/20261010T060607459741Z.json'
ISSUE = Path('research/experiments/data_issues/dart_list_survivorship_20261017T095252981370Z.json')
LABEL = '사후 진단·판정에 쓰지 않음'
REASONS = ('duplicate_ids', 'unknown_ids', 'schema', 'tool_use', 'other')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def summary(values):
    values = [float(v) for v in values if v is not None and math.isfinite(v)]
    return {'count': len(values), 'mean': float(np.mean(values)) if values else None,
            'std': float(np.std(values, ddof=1)) if len(values) > 1 else None,
            'min': min(values) if values else None, 'max': max(values) if values else None}


def reason_counts(values):
    counts = Counter(values)
    return {key: counts[key] for key in REASONS}


def attempt_reasons(attempt):
    """First detected validation failure only; invalid raw outputs were not saved."""
    if attempt['state'] == 'valid':
        return []
    error = attempt.get('error', '')
    violations = (attempt.get('event_summary') or {}).get('violations', [])
    reasons = []
    if 'duplicate candidate IDs' in error:
        reasons.append('duplicate_ids')
    if 'unknown candidate IDs' in error:
        reasons.append('unknown_ids')
    if 'ValidationError:' in error or 'wrong ranked/avoid list length' in error:
        reasons.append('schema')
    if any(v.startswith('forbidden_or_unknown_item:') for v in violations):
        reasons.append('tool_use')
    return reasons or ['other']


class SnapshotStream(_JSONStream):
    """Reuse bounded decoder, retaining compact returns and one input bundle."""
    def records(self, prefix=()):
        self.take('{')
        if self.peek() != '}':
            while True:
                key = self.value()
                self.take(':')
                path = prefix + (key,)
                if path in {('data', 'observations'), ('data', 'prepared')}:
                    for item in self.array():
                        yield path[-1], item
                elif path == ('data',):
                    yield from self.records(path)
                else:
                    yield '.'.join(path), self.value()
                if self.peek() == '}':
                    break
                self.take(',')
        self.take('}')


def later_delisted(reference):
    """Optional explicitly authorised local identity join; never scans prices."""
    profiles = {}
    for line in (reference / 'dart_company/companies.jsonl').read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        corp, cls = row['corp_code'], row.get('corp_cls')
        if corp in profiles and profiles[corp] != cls:
            raise ValueError('conflicting company classifications')
        profiles[corp] = cls
    with (reference / 'corp_codes.csv').open(encoding='utf-8-sig', newline='') as handle:
        return {r['stock_code'].strip() for r in csv.DictReader(handle) if profiles.get(r['corp_code']) == 'E'}


def call_diagnostic(call):
    attempts = []
    for ordinal, attempt in enumerate(call['attempts'], 1):
        attempts.append({'attempt': ordinal, 'call_number': attempt['call_number'], 'state': attempt['state'],
                         'seconds': attempt['seconds'], 'reasons': attempt_reasons(attempt),
                         'recorded_error': attempt.get('error'),
                         'event_violations': (attempt.get('event_summary') or {}).get('violations', [])})
    return {'key': call['key'], 'repeat_index': call['repeat_index'], 'valid': call['valid'],
            'attempts': attempts, 'invalid_attempts': sum(a['state'] == 'invalid' for a in attempts),
            'reasons': sorted({r for a in attempts for r in a['reasons']})}


def invalid_summary(calls):
    attempts = [a for c in calls for a in c['attempts']]
    return {'calls': len(calls), 'valid_calls': sum(c['valid'] for c in calls),
            'invalid_calls': sum(not c['valid'] for c in calls), 'attempts': len(attempts),
            'invalid_attempts': sum(a['state'] == 'invalid' for a in attempts),
            'interrupted_attempts': sum(a['state'] == 'interrupted' for a in attempts),
            'by_reason_attempts': reason_counts(r for a in attempts for r in a['reasons']),
            'by_reason_calls': reason_counts(r for c in calls for r in c['reasons']),
            'by_repeat_attempt': {f'{repeat}/{attempt}': reason_counts(
                r for c in calls if c['repeat_index'] == repeat for a in c['attempts']
                if a['attempt'] == attempt for r in a['reasons'])
                for repeat, attempt in sorted({(c['repeat_index'], a['attempt']) for c in calls for a in c['attempts']})}}


def performance(observations, chosen, affected):
    executable = {c: r for c, r in observations.items() if r['gross'] is not None and math.isfinite(r['gross'])}
    net = {c: r['gross'] - r['cost_1.0'] for c, r in executable.items()}
    a_codes = [c for c, r in executable.items() if r['phase'] == 2]
    selected = [c for c in chosen if c in executable]
    a_net = summary(net[c] for c in a_codes)['mean']
    selected_net = summary(net[c] for c in selected)['mean']
    excluded_selected = [c for c in selected if c not in affected]
    excluded_a = [c for c in a_codes if c not in affected]
    clean_net = summary(net[c] for c in excluded_selected)['mean']
    clean_a = summary(net[c] for c in excluded_a)['mean']
    paired = selected_net - a_net if selected_net is not None and a_net is not None else None
    # Exact additive decomposition of mean(C - A): affected C weights minus affected A weights.
    contribution = (sum(net[c] for c in selected if c in affected) / len(selected)
                    - sum(net[c] for c in a_codes if c in affected) / len(a_codes)) if paired is not None else None
    bought_contribution = sum(net[c] - a_net for c in selected if c in affected) / len(selected) if paired is not None else None
    universe = summary(r['gross'] for r in executable.values())
    return {'universe_gross': universe['mean'], 'universe_executable': len(executable), 'a_net': a_net,
            'a_executable': len(a_codes), 'selected_net': selected_net, 'selected_executable': len(selected),
            'a_excess_vs_universe': a_net - universe['mean'] if a_net is not None and universe['mean'] is not None else None,
            'holding_return_cross_sectional_std': universe['std'], 'paired_vs_a': paired,
            'affected_selected_executable': len(set(selected) & affected),
            'affected_a_executable': len(set(a_codes) & affected),
            'affected_additive_paired_contribution': contribution,
            'affected_buys_excess_contribution': bought_contribution,
            'c_excluded_only_vs_original_a': clean_net - a_net if clean_net is not None and a_net is not None else None,
            'both_excluded_paired': clean_net - clean_a if clean_net is not None and clean_a is not None else None,
            'unaffected_selected_executable': len(excluded_selected),
            'affected_stock_decisions': [{'stock_code': c, 'net': net[c],
                'c_excess_contribution': (net[c] - a_net) / len(selected) if c in selected and paired is not None else 0,
                'c_weight': 1 / len(selected) if c in selected else 0,
                'a_weight': 1 / len(a_codes) if c in a_codes else 0}
                for c in sorted((set(selected) | set(a_codes)) & affected)]}


def identify_affected_buys(selected, zero_codes, prior):
    """Affected is a subset of zero_codes, certified by the saved issue report.

    A zero affected count identifies the empty set. If affected count equals
    the zero-disclosure buy count, that entire set is affected. Interior counts
    cannot establish identities and must remain unmeasured.
    """
    count = prior['aggregate_selected']['affected']
    zeros = set(selected) & zero_codes
    if count == 0:
        return set()
    if count == len(zeros):
        return zeros
    return None


def features(bundle, pool):
    pool_set = set(pool)
    rows = [s for s in bundle['stocks'] if s['stock_code'] in pool_set]
    cols = [c for c in inputs.STOCK_COLUMNS if c not in {'id', 'industry', 'listing_coverage', 'quarters_4'}]
    missing = {c: sum(s.get(c) is None for s in rows) for c in cols}
    quarter_cells = [q.get(c) for s in rows for q in s['quarters_4']
                     for c in ('revenue_ratio', 'income_ratio', 'revenue_yoy', 'operating_income_yoy')]
    absent_quarter_cells = sum(4 * (4 - len(s['quarters_4'])) for s in rows)
    if absent_quarter_cells < 0:
        raise ValueError('more than four quarterly trend rows')
    total = len(rows) * len(cols) + len(quarter_cells) + absent_quarter_cells
    missing_cells = sum(missing.values()) + sum(v is None for v in quarter_cells) + absent_quarter_cells
    return {'candidate_count': len(rows), 'candidate_codes': pool,
            'industry_counts': dict(Counter(s['industry'] for s in rows)),
            'phase_counts': dict(Counter(str(s['phase']) for s in rows)),
            'missing_feature_cells': missing_cells, 'absent_quarter_cells': absent_quarter_cells,
            'feature_cells': total,
            'missing_feature_share': missing_cells / total if total else None,
            'missing_by_feature': missing,
            'candidate_mean_volatility_60': summary(s['volatility_60'] for s in rows)['mean'],
            'candidate_mean_market_cap_percentile': summary(s['market_cap_percentile'] for s in rows)['mean'],
            'candidate_mean_adv_20': summary(s['adv_20'] for s in rows)['mean']}


def compare_groups(rows):
    scalar = ('universe_gross', 'a_net', 'a_excess_vs_universe', 'holding_return_cross_sectional_std', 'candidate_count',
              'missing_feature_share', 'prompt_chars', 'rendered_chars', 'first_call_number',
              'decision_ordinal', 'candidate_mean_volatility_60', 'candidate_mean_market_cap_percentile',
              'candidate_mean_adv_20')
    groups = {}
    for valid in (True, False):
        group = [r for r in rows if r['valid_selection'] == valid]
        industry = Counter()
        phases = Counter()
        for r in group:
            industry.update(r['industry_counts'])
            phases.update(r['phase_counts'])
        groups['valid' if valid else 'no_selection'] = {
            'decisions': len(group), 'dates': [r['decision_date'] for r in group],
            'metrics': {k: summary(r[k] for r in group) for k in scalar},
            'industry_counts': dict(industry), 'phase_counts': dict(phases),
            'industry_shares_pooled': {k: v / sum(industry.values()) for k, v in industry.items()},
            'phase_shares_pooled': {k: v / sum(phases.values()) for k, v in phases.items()},
            'by_decision_year': dict(Counter(r['decision_date'][:4] for r in group))}
    groups['valid_minus_no_selection'] = {k: (groups['valid']['metrics'][k]['mean'] - groups['no_selection']['metrics'][k]['mean'])
        if all(groups[g]['metrics'][k]['mean'] is not None for g in ('valid', 'no_selection')) else None for k in scalar}
    return groups


def auxiliary(rows, arm):
    result = {}
    for policy in ('cash', 'group_a'):
        returns, excess = [], []
        for r in rows:
            value = r['selected_net'] if r['valid_selection'] else (0.0 if policy == 'cash' else r['a_net'])
            benchmark = r['universe_gross'] if arm == 'b_num' else r['a_net']
            returns.append(value)
            excess.append(value - benchmark if value is not None and benchmark is not None else None)
        result[policy] = {'net': summary(returns), 'paired_excess': summary(excess)}
    return {'label': LABEL, 'aggregation': 'arithmetic mean of decision bundles; no compounded NAV; cash return=0, no cash interest/cost',
            'failed_decisions': sum(not r['valid_selection'] for r in rows), 'policies': result}


def budget_projection(calls, budget, decisions=36):
    stages = {}
    for template in ('b_stage1', 'b_num', 'c_num'):
        group = [c for c in calls.values() if c['stage'] == 'screening' and c['prompt_version'].split('/')[1] == f'v1_{template}.txt']
        attempts = [a for c in group for a in c['attempts']]
        p = sum(a['state'] == 'valid' for a in attempts) / len(attempts)
        success = 1 - (1 - p) ** 3
        stages[template] = {'calls': len(group), 'attempts': len(attempts), 'attempt_validity': p,
                            'observed_repeat_validity': sum(c['valid'] for c in group) / len(group),
                            'expected_attempts_per_repeat_cap3': 1 + (1-p) + (1-p)**2,
                            'expected_repeat_validity_cap3': success,
                            'expected_all_three_repeats_valid': success**3,
                            'expected_attempts_three_successes_unbounded': 3 / p if p else None}
    s, b, c = (stages[k] for k in ('b_stage1', 'b_num', 'c_num'))
    # Text validity is not observed in screening. Numeric rates are an explicit scenario, not evidence for text.
    route = {'b_stage1': 1., 'b_num': s['expected_all_three_repeats_valid'],
             'b_text': s['expected_all_three_repeats_valid'] * b['expected_all_three_repeats_valid'],
             'c_num': 1., 'c_text': c['expected_all_three_repeats_valid']}
    counts = {k: decisions * 3 * gate * stages['b_num' if k == 'b_text' else 'c_num' if k == 'c_text' else k]['expected_attempts_per_repeat_cap3']
              for k, gate in route.items()}
    minimum = decisions * 15
    used = sum(len(c['attempts']) for c in calls.values())
    if used != budget['used']:
        raise ValueError('budget used differs from all cached real attempts')
    return {'used': used, 'cap': budget['cap'], 'remaining': budget['cap'] - used,
            'cached_call_records': len(calls), 'screening_rates': stages, 'final_decisions': decisions,
            'final_no_retry_calls_all_five_routes': minimum, 'final_retry_upper_bound': minimum * 3,
            'final_calls_if_all_routes_attempted_numeric_proxy': decisions * 3 * (
                s['expected_attempts_per_repeat_cap3'] + 2*b['expected_attempts_per_repeat_cap3'] + 2*c['expected_attempts_per_repeat_cap3']),
            'final_expected_calls_with_dependency_gates_numeric_proxy': sum(counts.values()),
            'final_expected_calls_by_route_numeric_proxy': counts,
            'shortfall_even_without_retries': max(0, minimum - (budget['cap'] - used)),
            'note': '36 decisions is preregistered planning case. Up to 2 retries per repeat, 3 independent repeats all valid required; no retry-until-three-successes. IID/stationarity assumptions; text validity unmeasured, numeric proxy scenario only. Candidate insufficiency ignored.'}


def calendar_decisions(clean_start):
    days = trading_calendar.session_calendar("2026-01-01", "2026-11-30")
    # Last session per Friday-ending week; same entry-inclusive holding rule as weekly_calendar.
    weeks = {}
    from datetime import timedelta
    for day in days:
        date = day.date()
        friday = date + timedelta(days=(4 - date.weekday()) % 7)
        weeks[friday] = date
    return [d.isoformat() for d in weeks.values() if clean_start <= d.isoformat() <= '2026-09-30'
            and days[days.get_loc(str(d)) + 20].date().isoformat() <= '2026-10-30']


def diagnose(*, repo=ROOT, data=None, result=None, issue=None, reference=None):
    started = time.perf_counter()
    data = repo / 'data/research/lh002' if data is None else data
    result_path = repo / RESULT if result is None else result
    issue_path = repo / ISSUE if issue is None else issue
    saved, issue_report = read(result_path), read(issue_path)
    if saved['stage'] != 'screening' or saved['smoke']:
        raise ValueError('requires finished official screening result')
    if issue_report['current_class_counts'] != {'E': issue_report['affected_stock_codes']}:
        raise ValueError('data issue affected classification differs from later-delisted E')
    affected_codes = later_delisted(reference) if reference is not None else None
    calls = {p.stem: read(p) for p in sorted((data / 'calls').glob('*.json'))}
    for key, c in calls.items():
        if key != c['key']:
            raise ValueError('call cache key mismatch')
    decisions = {d['decision_date']: d for d in saved['decisions']}
    if len(decisions) != len(saved['decisions']):
        raise ValueError('duplicate saved decision dates')
    issue_days = {d['decision_date']: d for d in issue_report['lh002']['per_decision']}
    observations = defaultdict(dict)
    rows = {a: [] for a in ('b_num', 'c_num')}
    stage1_calls = []
    extras = {}
    seen = set()
    prompt_dir = repo / EXPERIMENT / 'prompts'
    with (data / 'screening_snapshot.json').open(encoding='utf-8', newline='') as handle:
        stream = SnapshotStream(handle)
        for kind, value in stream.records():
            if kind == 'observations':
                day, code = value['decision_date'], value['stock_code']
                if code in observations[day]:
                    raise ValueError('duplicate stock-decision return')
                observations[day][code] = {k: value[k] for k in ('gross', 'cost_1.0', 'phase')}
            elif kind == 'prepared':
                bundle = value['bundle']
                day = bundle['decision_date']
                if day in seen or day not in observations:
                    raise ValueError('duplicate prepared decision or returns absent before prepared bundle')
                seen.add(day)
                if day > '2025-11-30':
                    raise ValueError('screening diagnostic must not read holdout decisions')
                decision = decisions[day]
                stocks = {s['stock_code']: s for s in bundle['stocks']}
                zero = {c for c, s in stocks.items() if all(s.get('disclosures_' + k) == 0 for k in inputs.CATEGORIES)}
                universe_issue = issue_days[day]['universe']
                if universe_issue['affected_candidates'] != universe_issue['affected_all_zero']:
                    raise ValueError('data issue does not certify all affected candidates have zero disclosures')
                affected = zero & affected_codes if affected_codes is not None else set()
                if affected_codes is not None and len(affected) != issue_days[day]['universe']['affected_all_zero']:
                    raise ValueError('reference classification does not reproduce data-issue exposure')
                stage1 = decision['arms']['b_stage1']
                industry_ids = {s['id'] for s in bundle['industries']}
                stage1_raw = [calls[k] for k in stage1['calls']]
                stage1_rendered, _ = inputs.render(bundle, anonymised=True, industry_only=True)
                for call in stage1_raw:
                    if call['stage'] != 'screening' or call['input_hash'] != inputs.digest(stage1_rendered):
                        raise ValueError('stage1 input mismatch')
                    if call['valid']:
                        arms.validate_selection(call['output'], industry_ids, count=3)
                stage1_selected = arms.aggregate([[r['id'] for r in c['output']['ranked']]
                    for c in stage1_raw], 3) if all(c['valid'] for c in stage1_raw) else []
                if stage1_selected != stage1['selected']:
                    raise ValueError('stage1 aggregate differs from cached calls')
                upstream = [call_diagnostic(calls[k]) for k in stage1['calls']]
                stage1_calls.extend(upstream)
                for arm in rows:
                    arm_saved = decision['arms'][arm]
                    sectors = {s['industry'] for s in bundle['industries'] if s['id'] in stage1_selected}
                    pool = arms.cap_codes(bundle, [c for c, s in stocks.items() if s['industry'] in sectors]) if arm == 'b_num' else arms.cap_codes(bundle, bundle['a_codes'])
                    rendered, mapping = inputs.render(bundle, anonymised=True, codes=pool, final_window=False)
                    template = f'v1_{arm}.txt'
                    instructions = (prompt_dir / 'v1_common.txt').read_text() + '\n' + (prompt_dir / template).read_text()
                    prompt = instructions + '\n<UNTRUSTED_DATA>\n' + rendered + '\n</UNTRUSTED_DATA>'
                    arm_calls = [calls[k] for k in arm_saved['calls']]
                    for call in arm_calls:
                        if call['stage'] != 'screening' or call['input_hash'] != inputs.digest(rendered):
                            raise ValueError('call stage/input differs from reconstructed snapshot prompt')
                        if call['prompt_version'] != 'v1/' + template + '/' + inputs.digest(instructions):
                            raise ValueError('cached prompt version differs from local prompt')
                        if call['valid']:
                            Draft202012Validator(read(prompt_dir / f'v1_{arm}.schema.json')).validate(call['output'])
                            arms.validate_selection(call['output'], set(mapping), count=30, avoid=10 if arm == 'c_num' else 0)
                    valid = bool(arm_calls) and all(c['valid'] for c in arm_calls)
                    rankings = [[r['id'] for r in c['output']['ranked']] for c in arm_calls] if valid else []
                    candidates = arms.aggregate(rankings, 30) if valid else []
                    selected = arms.aggregate([r[:10] for r in rankings], 10, eligible=set(candidates)) if valid else []
                    candidates, selected = [mapping[c] for c in candidates], [mapping[c] for c in selected]
                    if candidates != arm_saved['candidates'] or selected != arm_saved['selected']:
                        raise ValueError('reconstructed selection differs from saved result')
                    detail = [call_diagnostic(c) for c in arm_calls]
                    numbers = [a['call_number'] for c in detail for a in c['attempts']]
                    prior = issue_days[day]['arms'][arm]
                    identified = (set(selected) & affected) if affected_codes is not None else identify_affected_buys(selected, zero, prior)
                    perf = performance(observations[day], selected, affected if affected_codes is not None else identified or set())
                    if affected_codes is None:
                        for key in ('affected_a_executable', 'affected_additive_paired_contribution', 'both_excluded_paired'):
                            perf[key] = None
                        # Only bought-stock identities are established by cardinality, not all A members.
                        perf['affected_stock_decisions'] = [r for r in perf['affected_stock_decisions'] if r['c_weight'] > 0]
                        if identified is None:
                            for key in ('affected_selected_executable', 'affected_buys_excess_contribution',
                                        'c_excluded_only_vs_original_a', 'unaffected_selected_executable', 'affected_stock_decisions'):
                                perf[key] = None
                    row = {'decision_date': day, 'decision_ordinal': len(rows[arm]) + 1,
                           'valid_selection': len(selected) == 10, 'no_selection_reason': arm_saved['reason'],
                           'calls': detail, 'upstream_stage1_calls': upstream if arm == 'b_num' else [],
                           'invalid_reasons': sorted({r for c in detail + (upstream if arm == 'b_num' else []) for r in c['reasons']}),
                           **features(bundle, pool), **perf, 'first_call_number': min(numbers) if numbers else None,
                           'rendered_chars': len(rendered) if arm_calls else None,
                           'prompt_chars': len(prompt) if arm_calls else None,
                           'affected_buy_ids_identified': identified is not None,
                           'affected_buy_codes': sorted(identified) if identified is not None else None,
                           'zero_disclosure_buy_codes': sorted(set(selected) & zero),
                           'exposure': {'affected_shown': prior['affected_all_zero'],
                               'top30_slots': len(candidates), 'top30_affected': prior['aggregate_ranked']['affected'],
                               'bought_top10_slots': len(selected), 'bought_top10_affected': prior['aggregate_selected']['affected']}}
                    if affected_codes is not None:
                        if (prior['affected_all_zero'] != len(set(pool) & affected)
                            or prior['aggregate_ranked']['affected'] != len(set(candidates) & affected)
                            or prior['aggregate_selected']['affected'] != len(set(selected) & affected)):
                            raise ValueError('reconstructed arm exposure differs from data-issue report')
                    expected = next((r for r in saved['variants'][arm]['per_decision'] if r['decision_date'] == day), None)
                    if expected is not None:
                        benchmark = perf['universe_gross'] if arm == 'b_num' else perf['a_net']
                        if not math.isclose(perf['selected_net'] - benchmark, expected['excess_1.0'], abs_tol=1e-12):
                            raise ValueError('return reconstruction differs from official result')
                    rows[arm].append(row)
            else:
                extras[kind] = value
        if stream.peek():
            raise ValueError('snapshot trailing content')
        snapshot_sha = stream.sha256.hexdigest()
    if extras['sha256'] != saved['snapshot_hash'] or snapshot_sha != issue_report['lh002']['snapshot_sha256']:
        raise ValueError('snapshot/result/data-issue provenance mismatch')
    if extras['metadata']['stage'] != 'screening' or extras['metadata'].get('limit') is not None:
        raise ValueError('requires full official screening snapshot')
    if any(len(v) != len(decisions) for v in rows.values()):
        raise ValueError('snapshot/result decision counts differ')
    arm_reports = {}
    for arm, records in rows.items():
        flattened = [c for r in records for c in r['calls']]
        arm_reports[arm] = {'invalid_outputs': {**invalid_summary(flattened),
            'decisions': len(records), 'no_valid_selection': sum(not r['valid_selection'] for r in records),
            'by_reason_decisions': reason_counts(r for row in records for r in row['invalid_reasons']),
            'by_reason_no_selection_decisions': reason_counts(r for row in records if not row['valid_selection'] for r in row['invalid_reasons']),
            'no_selection_by_reason': dict(Counter(r['no_selection_reason'] for r in records if not r['valid_selection']))},
            'selection_comparison': compare_groups(records), 'full_period_auxiliary': auxiliary(records, arm),
            'survivorship': {'available': True, 'full_universe_ids_available': affected_codes is not None,
                'buy_ids_identified_decisions': sum(r['affected_buy_ids_identified'] for r in records),
                'exposure_source': 'saved data-issue report, tied to identical snapshot file SHA256',
                'exposure': {k: sum(r['exposure'][k] for r in records) for k in records[0]['exposure']}},
            'per_decision': records}
    c_rows = [r for r in rows['c_num'] if r['valid_selection'] and r['paired_vs_a'] is not None]
    survivor = arm_reports['c_num']['survivorship']
    survivor.update({'original_mean_paired_excess': summary(r['paired_vs_a'] for r in c_rows),
            'contribution_identified_all_paired_decisions': all(r['affected_buy_ids_identified'] for r in c_rows),
            'affected_buys_excess_contribution': summary(r['affected_buys_excess_contribution'] for r in c_rows),
            'affected_additive_paired_contribution': summary(r['affected_additive_paired_contribution'] for r in c_rows),
            'c_excluded_only_vs_original_a': summary(r['c_excluded_only_vs_original_a'] for r in c_rows),
            'both_excluded_paired': summary(r['both_excluded_paired'] for r in c_rows),
            'drop_decisions_with_affected_buys': summary(r['paired_vs_a'] for r in c_rows if not r['exposure']['bought_top10_affected']),
            'contribution_definition': 'Bought-stock excess attribution: sum affected buys (net_i - A_net) / executable C count, then mean over all paired decisions (unexposed decisions contribute zero). Exclusion reweights C survivors against unchanged original A. Without reference, affected buy IDs established only when affected count is zero or equals all-zero-disclosure buy count; ambiguous identities remain null. Full C/A affected decomposition and both-excluded results require reference.'})
    projection = budget_projection(calls, read(data / 'budget.json'))
    probe = read(data / 'probe.json')
    actual_days = calendar_decisions(probe['clean_start'])
    projection['calendar_case'] = {'source': 'local XKRX calendar, saved probe clean_start; no market data',
        'dates': actual_days, **budget_projection(calls, read(data / 'budget.json'), len(actual_days))}
    report = {'label': LABEL, 'status': 'dry_run', 'experiment_id': saved['experiment_id'],
        'sources': {'result': str(result_path), 'issue': str(issue_path), 'snapshot_file_sha256': snapshot_sha,
            'snapshot_data_hash_recorded': extras['sha256'], 'reference': str(reference) if reference else None,
            'result_sha256': hashlib.sha256(result_path.read_bytes()).hexdigest(),
            'issue_sha256': hashlib.sha256(issue_path.read_bytes()).hexdigest()},
        'holding_market_daily_volatility': {'available': False, 'value': None,
            'reason': 'Daily holding-window market path is absent from the frozen snapshot. Cross-sectional holding-return dispersion and pre-decision volatility_60 reported separately.'},
        'limitations': ['사후 기술통계이며 판정·인과 검정·새 실험이 아님. 기존 판정은 변경하지 않음.',
            '무효 응답 본문은 저장되지 않아 최초 검출 오류만 집계. 중복·미등록 ID 동시 발생 여부는 확인 불가.',
            '시장 수익은 같은 보유기간 실행가능 유니버스 동일가중 총수익. 보유기간 일별 시장 변동성은 스냅샷에 경로가 없어 측정 불가.',
            'holding_return_cross_sectional_std는 보유수익률의 종목 간 표준편차이며 일별 시장 변동성이 아님. volatility_60은 결정 전 입력.',
            '실제 호출 일시는 캐시에 없음. call_number는 전체 실행 순서이며 decision_date는 투자 결정 기준일.',
            'missing_feature_share는 제시 종목의 숫자 셀 및 4분기×4개 추이 숫자 셀 중 None 비율. 없는 분기 행은 결측 셀에 포함. 산업명·ID·coverage 메타데이터는 분모 제외.',
            '영향 정의: 기존 data-issue 보고서의 E(나중 상장폐지) 종목. 전부 공시 전 항목 0임을 기존 보고서로 확인. 영향 노출은 stock-decision 단위; 같은 종목 반복 포함.',
            '현금/A 대체는 비판정 보조치. 월별 20거래일 묶음 산술평균이며 투자 가능한 연속 NAV가 아님.',
            'reference 없이 매수 영향 ID는 영향 매수 수=0 또는 공시 전 항목 0인 매수 수와 일치할 때만 식별. 나머지는 측정 불가. A 전체 영향 제거는 참조 파일 없으면 측정 불가.'],
        'stage1_invalid_outputs': invalid_summary(stage1_calls), 'arms': arm_reports, 'budget': projection}
    if reference is not None:
        report['sources']['reference_sha256'] = {p: hashlib.sha256((reference / p).read_bytes()).hexdigest()
            for p in ('corp_codes.csv', 'dart_company/companies.jsonl')}
    report['performance'] = {'runtime_seconds': time.perf_counter() - started,
                             'peak_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024}
    return report


def korean_summary(report):
    def fmt(value):
        return '측정 불가' if value is None else f'{value:.8f}'
    lines = [f'# LH002 screening: {LABEL}', '', 'PAPER 연구용 사후 기술통계. 원래 판정·결과·사전등록·대장을 바꾸지 않습니다.', '',
             '| arm | 결정 | 선택 없음 | 유효 호출 | 무효 시도 | 중복 ID | 후보 밖 ID | schema | tool |',
             '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for arm, value in report['arms'].items():
        inv = value['invalid_outputs']
        reasons = inv['by_reason_attempts']
        lines.append(f"| {arm} | {inv['decisions']} | {inv['no_valid_selection']} | {inv['valid_calls']} | {inv['invalid_attempts']} | "
                     f"{reasons['duplicate_ids']} | {reasons['unknown_ids']} | {reasons['schema']} | {reasons['tool_use']} |")
    lines += ['', '오류는 최초 검출 기준이며, 회복한 호출도 무효 시도에 포함합니다. B 업종 단계는 JSON에 별도 집계합니다.', '',
              '| arm | 지표 | 선택 유효 평균 | 선택 없음 평균 | 차이 |', '| --- | --- | ---: | ---: | ---: |']
    for arm, value in report['arms'].items():
        cmp = value['selection_comparison']
        for key in cmp['valid_minus_no_selection']:
            lines.append(f"| {arm} | {key} | {cmp['valid']['metrics'][key]['mean']} | {cmp['no_selection']['metrics'][key]['mean']} | {cmp['valid_minus_no_selection'][key]} |")
    lines += ['', '후보 업종·국면 구성, 연도별 분포, 결정일·호출 순서·반복/시도별 상세는 JSON에 기록했습니다.', '',
              '| arm | 실패 결정 대체 | 전체 결정 평균 순수익 | 평균 짝 초과수익 |', '| --- | --- | ---: | ---: |']
    for arm, value in report['arms'].items():
        for policy, stats in value['full_period_auxiliary']['policies'].items():
            lines.append(f"| {arm} | {policy} | {fmt(stats['net']['mean'])} | {fmt(stats['paired_excess']['mean'])} |")
        exposure = value['survivorship']
        lines += ['', f"{arm} 생존편향 노출(후보 30 / 실제 매수 10 분리): `{exposure['exposure']}`"]
    survivor = report['arms']['c_num']['survivorship']
    if survivor['available']:
        for key in ('original_mean_paired_excess', 'affected_buys_excess_contribution', 'affected_additive_paired_contribution', 'c_excluded_only_vs_original_a', 'both_excluded_paired', 'drop_decisions_with_affected_buys'):
            lines += [f"- C {key}: {fmt(survivor[key]['mean'])} (결정 {survivor[key]['count']})"]
    else:
        lines += ['영향 종목 ID가 지정 자료에 없어 정확한 기여 측정 불가. 참조 파일 허용 후 재실행이 필요합니다.']
    budget = report['budget']
    lines += ['', f"예산: 사용 {budget['used']}, 잔여 {budget['remaining']}, 상한 {budget['cap']}.",
        f"사전등록 계획 36결정: 무재시도 {budget['final_no_retry_calls_all_five_routes']}, 최대 {budget['final_retry_upper_bound']}, "
        f"분기 실패 반영 숫자 유효율 대입 시나리오 기대 {budget['final_expected_calls_with_dependency_gates_numeric_proxy']:.2f}회.",
        f"로컬 달력 {budget['calendar_case']['final_decisions']}결정: 무재시도 {budget['calendar_case']['final_no_retry_calls_all_five_routes']}회, "
        f"분기 반영 시나리오 {budget['calendar_case']['final_expected_calls_with_dependency_gates_numeric_proxy']:.2f}회.",
        '반복마다 최대 3시도, 독립 반복 3개 모두 유효해야 선택. text 유효율은 미측정이며 숫자 그룹 유효율 대입은 예측 가정입니다.', '']
    for stage, rate in budget['screening_rates'].items():
        lines += [f"- {stage}: 시도 유효율 {rate['attempt_validity']:.2%}, 반복 성공 관측 {rate['observed_repeat_validity']:.2%}, "
                  f"최대 3시도 모형의 반복 3개 모두 성공 {rate['expected_all_three_repeats_valid']:.2%}"]
    lines += ['', *['- ' + text for text in report['limitations']], '',
              f"실행시간 {report['performance']['runtime_seconds']:.2f}s; 최대 RSS {report['performance']['peak_rss_mib']:.2f} MiB.", '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--reference-dir', type=Path, help='Explicit opt-in to local corp_codes.csv and dart_company/companies.jsonl for affected identities')
    args = parser.parse_args(argv)
    report = diagnose(reference=args.reference_dir)
    paths = []
    if args.execute:
        report['status'] = 'published'
        folder = ROOT / EXPERIMENT / 'diagnostics'
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        stem = folder / ('screening_diagnostic_' + stamp)
        encoded, markdown = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n', korean_summary(report)
        folder.mkdir(parents=True, exist_ok=True)
        paths = [stem.with_suffix('.json'), stem.with_suffix('.md')]
        with paths[0].open('x', encoding='utf-8') as jf:
            with paths[1].open('x', encoding='utf-8') as mf:
                jf.write(encoded)
                mf.write(markdown)
    print(json.dumps({'label': LABEL, 'status': report['status'], 'paths': list(map(str, paths)),
                      'performance': report['performance'], 'budget_remaining': report['budget']['remaining'],
                      'arms': {a: v['invalid_outputs'] for a, v in report['arms'].items()}}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
