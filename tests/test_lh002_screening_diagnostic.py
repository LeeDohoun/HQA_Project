import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts.research import lh002_screening_diagnostic as diagnostic


def test_invalid_attempts_first_failure_recovery_and_tool_schema():
    attempts = [
        {'state': 'invalid', 'error': 'ValueError: duplicate candidate IDs'},
        {'state': 'invalid', 'error': 'ValueError: unknown candidate IDs'},
        {'state': 'invalid', 'error': 'ValidationError: required ranked'},
        {'state': 'invalid', 'event_summary': {'violations': ['forbidden_or_unknown_item:command_execution']}},
        {'state': 'interrupted', 'error': 'quota'}, {'state': 'valid'}]
    assert [diagnostic.attempt_reasons(a) for a in attempts] == [
        ['duplicate_ids'], ['unknown_ids'], ['schema'], ['tool_use'], ['other'], []]
    call = {'key': 'x', 'repeat_index': 0, 'valid': True, 'attempts': [
        {**a, 'call_number': i, 'seconds': 1} for i, a in enumerate(attempts[:2] + attempts[-1:], 1)]}
    data = diagnostic.call_diagnostic(call)
    result = diagnostic.invalid_summary([data])
    assert result['valid_calls'] == 1 and result['invalid_calls'] == 0
    assert result['invalid_attempts'] == 2
    assert result['by_reason_calls']['duplicate_ids'] == 1
    assert result['by_repeat_attempt']['0/2']['unknown_ids'] == 1


def test_exposure_decomposition_and_reweighting_are_different():
    obs = {str(i): {'gross': v, 'cost_1.0': .01, 'phase': 2}
           for i, v in enumerate([.5, .1, .2, .3])}
    obs['missing'] = {'gross': None, 'cost_1.0': None, 'phase': 2}
    result = diagnostic.performance(obs, ['0', '1', 'missing'], {'0'})
    assert result['selected_net'] == pytest.approx(.29)
    assert result['a_net'] == pytest.approx(.265)
    assert result['affected_additive_paired_contribution'] == pytest.approx(.49/2 - .49/4)
    assert result['affected_buys_excess_contribution'] == pytest.approx((.49 - .265) / 2)
    assert result['c_excluded_only_vs_original_a'] == pytest.approx(.09 - .265)
    assert result['both_excluded_paired'] == pytest.approx(.09 - .19)
    assert result['selected_executable'] == 2


def test_full_period_cash_and_group_a_use_failed_decisions_not_missing_zero():
    rows = [{'valid_selection': True, 'selected_net': .10, 'a_net': .02, 'universe_gross': .01},
            {'valid_selection': False, 'selected_net': None, 'a_net': -.04, 'universe_gross': -.03}]
    c = diagnostic.auxiliary(rows, 'c_num')['policies']
    assert c['cash']['net']['mean'] == pytest.approx(.05)
    assert c['cash']['paired_excess']['mean'] == pytest.approx(.06)
    assert c['group_a']['paired_excess']['mean'] == pytest.approx(.04)
    b = diagnostic.auxiliary(rows, 'b_num')['policies']
    assert b['cash']['paired_excess']['mean'] == pytest.approx(.06)
    assert b['group_a']['paired_excess']['mean'] == pytest.approx(.04)


def test_budget_capped_retry_probability_and_all_repeats_required():
    calls = {name: {'stage': 'screening', 'valid': True, 'prompt_version': f'v1/v1_{name}.txt/x',
                    'attempts': [{'state': 'invalid'}, {'state': 'valid'}]}
             for name in ('b_stage1', 'b_num', 'c_num')}
    result = diagnostic.budget_projection(calls, {'used': 6, 'cap': 1100})
    rate = result['screening_rates']['c_num']
    assert rate['attempt_validity'] == .5
    assert rate['expected_attempts_per_repeat_cap3'] == 1.75
    assert rate['expected_all_three_repeats_valid'] == pytest.approx(.875**3)
    assert result['final_no_retry_calls_all_five_routes'] == 540
    assert result['final_retry_upper_bound'] == 1620
    assert result['final_expected_calls_with_dependency_gates_numeric_proxy'] < result['final_calls_if_all_routes_attempted_numeric_proxy']
    with pytest.raises(ValueError, match='budget'):
        diagnostic.budget_projection(calls, {'used': 7, 'cap': 1100})


def test_bounded_stream_unicode_chunks_observations_and_prepared():
    class Chunks(io.StringIO):
        def read(self, n):
            return super().read(min(n, 13))
    obj = {'data': {'observations': [{'code': '공시 [}]"\\', 'gross': 123456789}],
                    'prepared': [{'bundle': {'decision_date': '2020-01-31'}}]}, 'sha256': 'x'}
    encoded = json.dumps(obj, ensure_ascii=False)
    stream = diagnostic.SnapshotStream(Chunks(encoded))
    records = list(stream.records())
    assert records[0] == ('observations', obj['data']['observations'][0])
    assert records[1] == ('prepared', obj['data']['prepared'][0])
    assert records[2] == ('sha256', 'x') and not stream.peek()
    assert stream.sha256.hexdigest() == hashlib.sha256(encoded.encode()).hexdigest()


def fixture(tmp_path):
    repo, data = tmp_path / 'repo', tmp_path / 'cache'
    data.mkdir()
    (data / 'calls').mkdir()
    prompt_dir = repo / diagnostic.EXPERIMENT / 'prompts'
    prompt_dir.mkdir(parents=True)
    source = diagnostic.ROOT / diagnostic.EXPERIMENT / 'prompts'
    for p in source.glob('v1*'):
        (prompt_dir / p.name).write_bytes(p.read_bytes())
    stocks = []
    for i in range(45):
        row = dict.fromkeys(diagnostic.inputs.STOCK_COLUMNS, .1)
        row.update(stock_code=f'{i:06d}', industry='산업', adv_20=100-i, name=f'기업{i}', phase=2,
                   quarters_4=[], trading_value_percentile=.5,
                   listing_coverage={'days': 60, 'expected': 60, 'complete': True})
        row.update({'disclosures_'+c: 0 if i == 20 else 1 for c in diagnostic.inputs.CATEGORIES})
        stocks.append(row)
    bundles, obs, decisions = [], [], []
    call_number = 0
    calls = {}
    for n, day in enumerate(('2020-01-31', '2020-02-28')):
        bundle = {'decision_date': day, 'market': [], 'industries': [{'id': 'GA', 'industry': '산업'}, {'id': 'GB', 'industry': '기타'}, {'id': 'GC', 'industry': '추가'}],
                  'stocks': stocks, 'a_codes': [s['stock_code'] for s in stocks]}
        bundles.append({'bundle': bundle, 'texts': {}})
        obs += [{'decision_date': day, 'stock_code': s['stock_code'], 'gross': .01*i, 'cost_1.0': .001, 'phase': 2}
                for i, s in enumerate(stocks)]
        recorded = {}
        for arm in ('b_stage1', 'b_num', 'c_num'):
            key = hashlib.sha256((arm+day).encode()).hexdigest()
            rendered, mapping = diagnostic.inputs.render(bundle, anonymised=True, industry_only=arm == 'b_stage1', final_window=False)
            ids = list(mapping)
            # Ranks use ID lexical aggregation; one repeat preserves original rank.
            valid = arm == 'b_stage1' or n == 1
            ranked = ['GA', 'GB', 'GC'] if arm == 'b_stage1' else ids[:30]
            output = {'ranked': [{'id': identity, 'reason': '자료', 'confidence': 3} for identity in ranked]}
            if arm == 'c_num':
                output['avoid'] = [{'id': identity, 'reason': '자료', 'confidence': 3} for identity in ids[30:40]]
            attempts = []
            for attempt in range(1 if valid else 3):
                call_number += 1
                attempts.append({'state': 'valid' if valid else 'invalid', 'call_number': call_number,
                                 'seconds': .1, 'error': None if valid else 'ValueError: duplicate candidate IDs',
                                 'event_summary': {'violations': []}})
            template = f'v1_{arm}.txt'
            instructions = (prompt_dir / 'v1_common.txt').read_text() + '\n' + (prompt_dir / template).read_text()
            call = {'key': key, 'stage': 'screening', 'input_hash': diagnostic.inputs.digest(rendered),
                    'repeat_index': 0, 'valid': valid, 'output': output if valid else None, 'attempts': attempts,
                    'prompt_version': 'v1/' + template + '/' + diagnostic.inputs.digest(instructions)}
            calls[key] = call
            (data / 'calls' / f'{key}.json').write_text(json.dumps(call))
            recorded[arm] = {'calls': [key], 'selected': ['GA', 'GB', 'GC'] if arm == 'b_stage1' else [mapping[k] for k in ranked[:10]] if valid else [],
                             'candidates': [mapping[k] for k in ranked] if valid and arm != 'b_stage1' else [],
                             'reason': None if valid else 'invalid_call'}
        decisions.append({'decision_date': day, 'arms': recorded})
    snapshot = {'data': {'observations': obs, 'prepared': bundles}, 'metadata': {'stage': 'screening'}, 'sha256': 'data-hash'}
    encoded = json.dumps(snapshot, ensure_ascii=False)
    (data / 'screening_snapshot.json').write_text(encoded)
    issue = {'current_class_counts': {'E': 1}, 'affected_stock_codes': 1, 'lh002': {
        'snapshot_sha256': hashlib.sha256(encoded.encode()).hexdigest(),
        'per_decision': [{'decision_date': b['bundle']['decision_date'], 'universe': {'affected_all_zero': 1, 'affected_candidates': 1},
            'arms': {arm: {'affected_all_zero': 1, 'aggregate_ranked': {'affected': n},
                           'aggregate_selected': {'affected': 0}} for arm in ('b_num', 'c_num')}}
                         for n, b in enumerate(bundles)]}}
    (tmp_path / 'issue.json').write_text(json.dumps(issue))
    result = {'stage': 'screening', 'smoke': False, 'snapshot_hash': 'data-hash', 'experiment_id': 'LH002_llm_hegemony_judge',
              'decisions': decisions, 'variants': {arm: {'per_decision': []} for arm in ('b_num', 'c_num')}}
    (tmp_path / 'result.json').write_text(json.dumps(result))
    (data / 'budget.json').write_text(json.dumps({'used': call_number, 'cap': 1100}))
    (data / 'probe.json').write_text(json.dumps({'clean_start': '2026-01-01'}))
    reference = tmp_path / 'reference'
    (reference / 'dart_company').mkdir(parents=True)
    (reference / 'corp_codes.csv').write_text('corp_code,stock_code\n00000001,000020\n')
    (reference / 'dart_company/companies.jsonl').write_text(json.dumps({'corp_code': '00000001', 'corp_cls': 'E'})+'\n')
    return dict(repo=repo, data=data, result=tmp_path/'result.json', issue=tmp_path/'issue.json', reference=reference)


def test_end_to_end_single_snapshot_read_no_writes_and_top30_not_buys(tmp_path, monkeypatch):
    args = fixture(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    original = Path.open
    opened = []
    def track(path, *a, **kw):
        if path.name.startswith('.env'):
            pytest.fail('must never read env files')
        if path.name == 'screening_snapshot.json':
            opened.append(path)
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, 'open', track)
    report = diagnostic.diagnose(**args)
    assert len(opened) == 1
    assert report['arms']['c_num']['invalid_outputs']['no_valid_selection'] == 1
    exposure = report['arms']['c_num']['survivorship']['exposure']
    assert exposure['top30_slots'] == 30 and exposure['bought_top10_slots'] == 10
    assert exposure['top30_affected'] == 1 and exposure['bought_top10_affected'] == 0
    assert report['arms']['c_num']['full_period_auxiliary']['policies']['cash']['net']['count'] == 2
    assert '사후 진단·판정에 쓰지 않음' in diagnostic.korean_summary(report)
    assert before == {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    args['reference'] = None
    unmeasured = diagnostic.diagnose(**args)
    assert unmeasured['arms']['c_num']['survivorship']['available']
    assert unmeasured['arms']['c_num']['survivorship']['contribution_identified_all_paired_decisions']
    assert unmeasured['arms']['c_num']['survivorship']['both_excluded_paired']['mean'] is None


def test_provenance_and_reference_mismatch_fail_clearly(tmp_path):
    args = fixture(tmp_path)
    (args['reference'] / 'dart_company/companies.jsonl').write_text(json.dumps({'corp_code': '00000001', 'corp_cls': 'Y'})+'\n')
    with pytest.raises(ValueError, match='classification'):
        diagnostic.diagnose(**args)
    args['reference'] = None
    issue = diagnostic.read(args['issue'])
    issue['lh002']['snapshot_sha256'] = 'wrong'
    args['issue'].write_text(json.dumps(issue))
    with pytest.raises(ValueError, match='provenance'):
        diagnostic.diagnose(**args)


def test_cli_default_dry_run_and_exclusive_publication(tmp_path, monkeypatch, capsys):
    args = fixture(tmp_path)
    original = diagnostic.diagnose
    monkeypatch.setattr(diagnostic, 'ROOT', tmp_path/'output')
    monkeypatch.setattr(diagnostic, 'diagnose', lambda **kw: original(**args))
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 17, tzinfo=timezone.utc)
    monkeypatch.setattr(diagnostic, 'datetime', FixedDatetime)
    assert diagnostic.main([]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'dry_run'
    assert not (tmp_path/'output').exists()
    assert diagnostic.main(['--execute']) == 0
    response = json.loads(capsys.readouterr().out)
    assert response['status'] == 'published' and len(response['paths']) == 2
    output = Path(response['paths'][0]).parent
    before = {p: p.read_bytes() for p in output.glob('*')}
    with pytest.raises(FileExistsError):
        diagnostic.main(['--execute'])
    assert before == {p: p.read_bytes() for p in output.glob('*')}


def test_b_upstream_failure_is_a_no_selection_not_an_invalid_numeric_call(tmp_path):
    args = fixture(tmp_path)
    saved = diagnostic.read(args['result'])
    first = saved['decisions'][0]['arms']
    key = first['b_stage1']['calls'][0]
    path = args['data']/'calls'/f'{key}.json'
    call = diagnostic.read(path)
    call['valid'] = False
    call['output'] = None
    call['attempts'][0].update(state='invalid', error='ValueError: unknown candidate IDs')
    path.write_text(json.dumps(call))
    first['b_stage1']['selected'] = []
    old_numeric = first['b_num']['calls'][0]
    (args['data']/'calls'/f'{old_numeric}.json').unlink()
    first['b_num'].update(calls=[], reason='invalid_stage1')
    args['result'].write_text(json.dumps(saved))
    issue = diagnostic.read(args['issue'])
    issue['lh002']['per_decision'][0]['arms']['b_num']['affected_all_zero'] = 0
    args['issue'].write_text(json.dumps(issue))
    budget_path = args['data']/'budget.json'
    budget = diagnostic.read(budget_path)
    budget['used'] -= 3
    budget_path.write_text(json.dumps(budget))
    report = diagnostic.diagnose(**args)
    b = report['arms']['b_num']
    assert b['invalid_outputs']['calls'] == 1
    assert b['invalid_outputs']['no_valid_selection'] == 1
    assert b['invalid_outputs']['by_reason_no_selection_decisions']['unknown_ids'] == 1
    assert report['stage1_invalid_outputs']['invalid_calls'] == 1


def test_affected_buy_identity_proof_leaves_interior_counts_unmeasured():
    selected = ['a', 'b', 'c']
    zeros = {'a', 'b'}
    assert diagnostic.identify_affected_buys(selected, zeros, {'aggregate_selected': {'affected': 0}}) == set()
    assert diagnostic.identify_affected_buys(selected, zeros, {'aggregate_selected': {'affected': 2}}) == zeros
    assert diagnostic.identify_affected_buys(selected, zeros, {'aggregate_selected': {'affected': 1}}) is None
