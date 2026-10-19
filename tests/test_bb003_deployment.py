"""Separate-root inventory, lock, mechanical amendments and offline rehearsal."""
import json
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta, time
import os
from pathlib import Path
import subprocess
import sys

import pytest

from backtesting.experiments.bb003 import admission, deployment, policy, recorder, storage
from test_bb003_recorder import env, fake_publication, operation_verification

ROOT = Path(__file__).resolve().parents[1]


def test_new_files_stay_outside_frozen_inventory():
    # Check BB003's own files only; other sessions' untracked drafts in a shared checkout are not BB003's.
    roots = ["backtesting/experiments/bb003", "scripts/bb003", "deploy/collector-bb003"]
    raw = subprocess.check_output(["git", "ls-files", "--", *roots, "tests/test_bb003_*.py"], cwd=ROOT, text=True,
                                  env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    names = raw.splitlines()
    assert names
    assert not [name for name in names if deployment.frozen_path(name)]
    assert not [name for name in names if Path(name).name == "preregistration.md"]


def test_manifest_import_closure_and_units_are_separate():
    names = deployment.inventory(ROOT)
    assert "backtesting/experiments/bb003/recorder.py" in names
    assert "backtesting/experiments/bb001.py" in names
    assert "src/ingestion/dart_buyback.py" in names
    assert not any(p.startswith(".env") or p.startswith("data/") or p.startswith("venv/") for p in names)
    unit = (ROOT / "deploy/collector-bb003/systemd/hqa-bb003-daily.service").read_text()
    assert "WorkingDirectory=/opt/hqa-bb003" in unit
    assert "InaccessiblePaths=-/opt/hqa" in unit
    assert [line for line in unit.splitlines() if line.startswith("ReadWritePaths=")] == ["ReadWritePaths=/var/lib/hqa/data/forward/bb003"]
    assert "MemoryMax=600M" in unit
    assert "/opt/hqa/venv" not in unit
    assert storage.sha((ROOT / deployment.PREREG).read_bytes()) == deployment.PREREG_SHA256


def test_isolated_inventory_imports_without_other_checkout(tmp_path):
    import shutil
    for name in deployment.inventory(ROOT):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, path)
    code = "import sys; sys.path.insert(0, sys.argv[1]); from backtesting.experiments.bb003 import recorder, evaluate; print(recorder.__file__)"
    output = subprocess.check_output([sys.executable, "-I", "-B", "-c", code, str(tmp_path)], cwd=tmp_path,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHON_DOTENV_DISABLED": "1", "OPENBLAS_NUM_THREADS": "1"}, text=True)
    assert output.strip() == str(tmp_path / "backtesting/experiments/bb003/recorder.py")


def test_main_checkout_and_state_symlink_writes_are_refused(tmp_path):
    with pytest.raises(ValueError, match="main checkout"):
        storage.Store(storage.MAIN / "data")
    (tmp_path / "forward").mkdir()
    (tmp_path / "forward/bb003").symlink_to(storage.MAIN)
    with pytest.raises(ValueError):
        storage.Store(tmp_path)
    for path in (ROOT / "data", ROOT / "src", Path("/opt/hqa"),
                 Path("/var/lib/hqa/data/forward/fw001"), Path("/var/lib/hqa/data/shadow")):
        with pytest.raises(ValueError, match="overlaps"):
            storage.Store(path)


def test_rehearsal_rejects_operational_and_source_roots(tmp_path):
    for output in (ROOT / "new-rehearsal", storage.MAIN / "other", Path("/opt/hqa-bb003/test"), tmp_path):
        with pytest.raises(ValueError):
            deployment.rehearsal_output(output, tmp_path / "source", ROOT)


def test_shared_heavy_lock_serializes_even_read_only(tmp_path):
    lock = tmp_path / "run.lock"
    lock.write_bytes(b"")
    with deployment.heavy_lock(lock):
        with pytest.raises(BlockingIOError):
            with deployment.heavy_lock(lock):
                pytest.fail("must not overlap")
    with deployment.heavy_lock(lock):
        pass


def test_daily_dry_run_is_write_free_and_offline(tmp_path, capsys):
    from scripts.bb003.__main__ import main
    output = tmp_path / "never-created"
    assert main(["record", "--dry-run", "--output-root", str(output)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "dry_run"
    assert not output.exists()


@pytest.mark.parametrize("bad", [None, "flock", "exec", "remain"])
def test_installed_FW001_shadow_lock_contract(bad):
    def run(command, **kwargs):
        return ("LoadState=loaded\nNeedDaemonReload=no\nRuntimeDirectoryPreserve=yes\nType="
                + ("exec" if bad == "exec" else "oneshot") + "\nRemainAfterExit="
                + ("yes" if bad == "remain" else "no") + "\nExecStart="
                + ("/opt/hqa/venv/bin/python" if bad == "flock" else "/usr/bin/flock --exclusive --no-fork /run/hqa-heavy/run.lock /opt/hqa/venv/bin/python") + "\n")
    if bad:
        with pytest.raises(ValueError, match="d22e231"):
            deployment.fw001_contract(run)
    else:
        assert len(deployment.fw001_contract(run)) == 2


def test_mechanical_policy_rejects_code_and_unofficial_sources(env):
    _, store, rules, _, _, _ = env
    with pytest.raises(ValueError, match="only calendar/tax"):
        policy.Policy({**rules.data, "code": "def altered(): pass"})
    with pytest.raises(ValueError, match="official"):
        policy.append(store, {**rules.data, "sources": [{"kind": "calendar", "url": "https://example.com/calendar",
            "sha256": "a" * 64, "published_at": "2026-01-01T00:00:00+09:00"}]}, reason="fake")


def test_official_data_only_policy_hash_chain(env):
    _, store, rules, _, _, _ = env
    notices = {storage.sha(b"calendar"): b"calendar", storage.sha(b"tax"): b"tax"}
    sources = [{"kind": kind, "url": url, "sha256": storage.sha(kind.encode()), "published_at": "2026-10-01T00:00:00+09:00",
        "fields": ["calendar_start", "calendar_through", "sessions"] if kind == "calendar" else ["tax_through", "tax.KOSPI", "tax.KOSDAQ"],
        "effective_from": "2026-01-01", "effective_through": "2029-12-31"}
        for kind, url in (("calendar", "https://global.krx.co.kr/synthetic-offline-notice"), ("tax", "https://law.go.kr/synthetic-offline-notice"))]
    data = {**rules.data, "sources": sources}
    policy.append(store, data, notices=notices, reason="synthetic test only")
    assert policy.latest(store).digest == policy.Policy(data).digest
    changed = {**data, "tax": data["tax"] + [{"from": "2027-01-01", "KOSPI": .003, "KOSDAQ": .003}]}
    policy.append(store, changed, notices=notices, reason="synthetic prospective tax update")
    assert len(store.keys("policy")) == 2
    assert store.get("policy", "000001")["previous_sha256"] == policy.Policy(data).digest
    assert policy.latest(store).digest == policy.Policy(changed).digest
    past_change = {**changed, "tax": [{"from": "2026-01-01", "KOSPI": .004, "KOSDAQ": .004}]}
    with pytest.raises(ValueError, match="elapsed tax"):
        policy.append(store, past_change, notices=notices, reason="invalid retroactive change")


def test_rehearsal_full_pipeline_fake_clock_in_fresh_root(tmp_path):
    from backtesting.experiments.bb003.rehearsal import rehearse
    output = tmp_path / "replay"
    result = rehearse(tmp_path / "unused", output, start="2025-07-01", end="2025-07-03", synthetic=True)
    assert result["fake_clock"] and result["network_calls"] == 0
    assert result["counts"]["included"] == 1
    assert result["peak_rss_mib"] < 1500
    assert {"listings", "fetch_and_verify", "pre_entry_features", "decision_seal", "post_entry"} <= set(result["phase_seconds"])
    store = storage.Store(output, measurement=True)
    assert len(store.keys("post_entry")) == 1
    assert all(store.get("decisions", k)["label"] == "reconstructed" for k in store.keys("decisions"))
    with pytest.raises(ValueError, match="mode"):
        storage.Store(output).proof("decisions", store.keys("decisions")[0])


def test_bugfix_journal_binds_before_after_and_rejects_frozen_changes(env):
    from backtesting.experiments.bb003.amendments import binding, record_bugfix
    _, store, _, _, clock, _ = env
    before = {"experiment_id": "BB003_buyback_forward", "code_root": "/opt/hqa-bb003", "data_root": "/var/lib/hqa/data",
              "files": {"backtesting/experiments/bb003/recorder.py": "a", "src/ingestion/dart_buyback.py": "frozen"}}
    # This independent test activation is bound to the fake original manifest.
    store = storage.Store(store.root.parent.parent / "bugfix-state", clock=store.clock)
    raw = storage.encoded(before)
    store.test_manifest_sha256 = storage.sha(raw)
    from backtesting.experiments.bb003 import recorder
    original_time = clock[0]
    clock[0] = storage.aware("2026-11-30T12:00:00+09:00")
    recorder.activate(store, commit="a" * 40, manifest_hash=storage.sha(raw))
    clock[0] = original_time
    after = {**before, "files": {**before["files"], "backtesting/experiments/bb003/recorder.py": "b"}}
    changed = storage.encoded(after)
    record_bugfix(store, raw, changed, reason="Fix deadline comparison", commit="c" * 40)
    assert binding(store, storage.sha(changed)) == storage.sha(changed)
    assert store.get("bugfixes", "000000")["reason"] == "Fix deadline comparison"
    bad = {**after, "files": {**after["files"], "src/ingestion/dart_buyback.py": "changed"}}
    with pytest.raises(ValueError, match="never FW001"):
        record_bugfix(store, changed, storage.encoded(bad), reason="forbidden", commit="d" * 40)


@pytest.mark.parametrize("damage", ["source", "environment", "unreviewed_manifest"])
def test_real_shared_verifier_rejects_source_environment_or_manifest_change(tmp_path, monkeypatch, operation_verification, fake_publication, damage):
    from backtesting.experiments.bb003 import recorder
    root, data = tmp_path / "installed", tmp_path / "data"
    module = "deploy/collector-bb003/environment.py"
    worker = "backtesting/experiments/bb003/worker.py"
    storage.put(root / module, b'def inventory(root):\n    return {"runtime": (root / "runtime").read_text()}\n')
    storage.put(root / worker, b"# sealed worker\n")
    storage.put(root / "venv/pyvenv.cfg", b"synthetic private test environment")
    storage.put(root / "venv/runtime", b"original")
    manifest = {"code_root": str(root), "data_root": str(data),
        "files": {p: storage.sha((root / p).read_bytes()) for p in (module, worker)},
        "environment_sha256": storage.sha(storage.encoded({"runtime": "original"}))}
    raw = storage.encoded(manifest)
    storage.put(root / deployment.MANIFEST, raw)
    monkeypatch.setattr(deployment, "__file__", str(root / "backtesting/experiments/bb003/deployment.py"))
    monkeypatch.setattr(deployment, "CODE_ROOT", root)
    monkeypatch.setattr(deployment, "DATA_ROOT", data)
    store = storage.Store(data, clock=lambda: fake_publication[0][0])
    store.test_manifest_sha256 = storage.sha(raw)
    recorder.activate(store, commit="a" * 40, manifest_hash=storage.sha(raw), manifest_raw=raw)
    assert operation_verification(store, mode="setup")["manifest_sha256"] == storage.sha(raw)
    if damage == "source":
        (root / worker).write_bytes(b"# changed worker\n")
    elif damage == "environment":
        (root / "venv/runtime").write_bytes(b"changed")
    else:
        (root / deployment.MANIFEST).write_bytes(storage.encoded({**manifest, "commit": "c" * 40}))
    with pytest.raises(ValueError, match="inventory differs|environment differs|bugfix journal"):
        operation_verification(store, mode="setup")


@pytest.mark.parametrize("operation", ["record", "seal", "post-entry", "capture", "evaluate"])
def test_all_function_entrypoints_verify_before_input_reads(env, monkeypatch, operation):
    from backtesting.experiments.bb003 import recorder, evaluate, inputs
    source, store, rules, client, _, _ = env
    def reject(*a, **k):
        raise ValueError("verification rejected")
    monkeypatch.setattr(deployment, "verify_operation", reject)
    monkeypatch.setattr(evaluate, "verify_operation", reject)
    monkeypatch.setattr(inputs, "read_bytes", lambda *a: pytest.fail("input read before verification"))
    calls = {"record": lambda: recorder.prepare_day(store, source, rules, client),
        "seal": lambda: recorder.seal_day(store, source, rules),
        "post-entry": lambda: recorder.post_entry(store, source, rules),
        "capture": lambda: recorder.capture_prices(store, source, rules),
        "evaluate": lambda: evaluate.evaluate(store, rules)}
    with pytest.raises(ValueError, match="verification rejected"):
        calls[operation]()


@pytest.mark.parametrize("command", ["record", "post-entry", "capture", "evaluate", "recover-evaluation", "publish-trial", "policy-init", "policy-amend", "interim"])
def test_manual_cli_cannot_bypass_shared_verification(tmp_path, monkeypatch, command):
    from scripts.bb003.__main__ import main
    def reject(*a, **k):
        raise ValueError("verification rejected")
    monkeypatch.setattr(deployment, "operational_lock", lambda **k: nullcontext())
    monkeypatch.setattr(deployment, "verify_operation", reject)
    output = tmp_path / "state"
    with pytest.raises(ValueError, match="verification rejected"):
        main([command, "--execute", "--output-root", str(output)])
    assert not output.exists()


def test_sealing_rechecks_verification_and_binds_every_record(tmp_path, monkeypatch, fake_publication):
    store = storage.Store(tmp_path, clock=lambda: fake_publication[0][0])
    calls = []
    def verify(*a, **k):
        calls.append(1)
        return {"manifest_sha256": "a" * 64 if len(calls) == 1 else "b" * 64}
    monkeypatch.setattr(deployment, "verify_operation", verify)
    with pytest.raises(ValueError, match="changed before sealing"):
        store.seal("sample", "changed", {"value": 1})
    assert len(calls) == 2 and store.proof("sample", "changed") is None
    monkeypatch.setattr(deployment, "verify_operation", lambda *a, **k: {"manifest_sha256": "b" * 64})
    store.seal("sample", "valid", {"value": 2})
    assert store.get("sample", "valid")["manifest_sha256"] == "b" * 64


class InstalledManager:
    """Effective manager responses, including KRX's four reviewed retries."""
    def __init__(self, clock):
        self.clock, self.active, self.pending, self.overrides, self.paths = clock, [], [], {}, {}
        self.times = {admission.TIMERS[0]: ["08:15:00", "16:15:00", "18:35:00"],
                      admission.TIMERS[1]: ["08:02:00", "08:12:00", "08:25:00", "08:40:00", "18:30:00"],
                      admission.TIMERS[2]: ["08:16:00", "18:40:00"],
                      admission.TIMERS[3]: ["08:00:00", "18:00:00"]}

    def __call__(self, command, **kwargs):
        stamp = self.clock[0]
        if command[0] == "systemd-analyze":
            base = storage.aware(next(v.split("=", 1)[1] for v in command if v.startswith("--base-time=")))
            result = datetime.combine(base.date(), time.fromisoformat(command[-1]), storage.aware(stamp).tzinfo)
            if result <= base:
                result += timedelta(days=1)
            return "Next elapse: " + result.astimezone(UTC).strftime("%a %Y-%m-%d %H:%M:%S UTC") + "\n"
        if command[1] == "list-units":
            if "--type=path" in command:
                return "\n".join(self.paths)
            return "\n".join(self.active)
        if command[1] == "list-jobs":
            return "\n".join(self.pending)
        unit = command[2]
        if unit == "hqa-krx-daily.service":
            return "LoadState=loaded\nNeedDaemonReload=no\nOnSuccess=hqa-fw001-daily.service hqa-shadow-daily.service\n"
        if unit in admission.FW002_SERVICES or unit in self.paths:
            fields = self.paths.get(unit, {"LoadState": "loaded", "NeedDaemonReload": "no"}).copy()
            fields.update(self.overrides.get(unit, {}))
            return "\n".join(k + "=" + v for k, v in fields.items()) + "\n"
        if unit == "hqa-fw002-fill.timer":
            fields = {"LoadState": "loaded", "NeedDaemonReload": "no", "ActiveState": "active",
                      "TimersCalendar": "", "TimersMonotonic": "{ OnUnitInactiveUSec=10min ; }",
                      "AccuracyUSec": "1s", "RandomizedDelayUSec": "0", "Persistent": "no",
                      "NextElapseUSecRealtime": "n/a", "NextElapseUSecMonotonic": str(int((admission.timer.monotonic() + 20) * 1e6)),
                      "LastTriggerUSec": "0"}
            fields.update(self.overrides.get(unit, {}))
            return "\n".join(k + "=" + v for k, v in fields.items()) + "\n"
        dates = [datetime.combine(stamp.date(), time.fromisoformat(t), stamp.tzinfo) for t in self.times[unit]]
        next_date = min([d for d in dates if d >= stamp] or [dates[0] + timedelta(days=1)])
        last_date = max([d for d in dates if d < stamp] or [dates[-1] - timedelta(days=1)])
        fields = {"LoadState": "loaded", "NeedDaemonReload": "no", "ActiveState": "active",
                  "TimersCalendar": " ".join("{ OnCalendar=" + t + " ; }" for t in self.times[unit]),
                  "TimersMonotonic": "", "AccuracyUSec": "1s", "RandomizedDelayUSec": "0",
                  "NextElapseUSecRealtime": next_date.isoformat(), "NextElapseUSecMonotonic": "0",
                  "Persistent": "yes", "LastTriggerUSec": last_date.isoformat()}
        fields.update(self.overrides.get(unit, {}))
        return "\n".join(k + "=" + v for k, v in fields.items()) + "\n"


@pytest.mark.parametrize("stamp,blocked", [("05:30:00", False), ("05:30:01", True), ("08:40:00", True),
    ("09:01:00", False), ("18:19:54", False), ("18:19:55", True)])
def test_bulk_preparation_protects_morning_and_installed_evening_triggers(stamp, blocked):
    clock = [storage.aware("2026-12-02T" + stamp + "+09:00")]
    manager = InstalledManager(clock)
    if blocked:
        with pytest.raises(policy.NotReady):
            deployment.deadline_priority(clock=lambda: clock[0], run=manager)
    else:
        deployment.deadline_priority(clock=lambda: clock[0], run=manager)


def test_preparation_tracks_installed_timer_changes_and_fails_on_missing_metadata():
    clock = [storage.aware("2026-12-02T18:10:00+09:00")]
    manager = InstalledManager(clock)
    deployment.deadline_priority(clock=lambda: clock[0], run=manager)
    manager.times[admission.TIMERS[1]].append("18:15:00")
    with pytest.raises(policy.NotReady, match="higher_priority_trigger"):
        deployment.deadline_priority(clock=lambda: clock[0], run=manager)
    with pytest.raises(ValueError, match="installed timer metadata"):
        deployment.deadline_priority(clock=lambda: clock[0], run=lambda *a, **k: "LoadState=not-found\n")


def test_lock_admission_checks_both_sides_before_any_environment_hash(monkeypatch):
    steps = []
    def guard(**kwargs):
        steps.append("priority")
        if len(steps) == 3:
            raise policy.NotReady("crossed into FW001 priority window")
    @contextmanager
    def lock():
        steps.append("lock")
        try:
            yield
        finally:
            steps.append("unlock")
    monkeypatch.setattr(deployment, "deadline_priority", guard)
    monkeypatch.setattr(deployment, "heavy_lock", lock)
    with pytest.raises(policy.NotReady, match="crossed into"):
        with deployment.operational_lock():
            pytest.fail("must not hash environment or start work")
    assert steps == ["priority", "lock", "priority", "unlock"]


def test_manual_worker_has_kernel_time_limit_even_outside_systemd(monkeypatch):
    steps = []
    monkeypatch.setattr(deployment, "deadline_priority", lambda **k: steps.append("priority"))
    monkeypatch.setattr(deployment, "heavy_lock", nullcontext)
    monkeypatch.setattr(deployment.signal, "getitimer", lambda *a: (0., 0.))
    monkeypatch.setattr(deployment.signal, "signal", lambda *a: deployment.signal.SIG_DFL)
    monkeypatch.setattr(deployment.signal, "setitimer", lambda which, seconds: steps.append(seconds))
    with deployment.operational_lock():
        steps.append("work")
    assert steps == [600, "priority", "priority", "work", 0]


def official_2026_policy(rules):
    notices = {storage.sha(b"calendar test notice"): b"calendar test notice", storage.sha(b"tax test notice"): b"tax test notice"}
    sources = [{"kind": kind, "url": url, "sha256": storage.sha((kind + " test notice").encode()),
        "published_at": "2026-10-01T00:00:00+09:00", "effective_from": "2026-01-01", "effective_through": "2026-12-31",
        "fields": ["calendar_start", "calendar_through", "sessions"] if kind == "calendar" else ["tax_through", "tax.KOSPI", "tax.KOSDAQ"]}
        for kind, url in (("calendar", "https://kind.krx.co.kr/offline-test"), ("tax", "https://law.go.kr/offline-test"))]
    return {**rules.data, "calendar_through": "2026-12-31", "tax_through": "2026-12-31",
        "sessions": rules.sessions[rules.sessions <= "2026-12-31"].strftime("%Y-%m-%d").tolist(), "sources": sources}, notices


@pytest.mark.parametrize("missing_kind", ["calendar", "tax"])
def test_initial_2026_baseline_requires_official_evidence_for_both_kinds(env, missing_kind):
    _, store, rules, _, _, _ = env
    data, notices = official_2026_policy(rules)
    data["sources"] = [s for s in data["sources"] if s["kind"] != missing_kind]
    with pytest.raises(ValueError, match="official evidence for changed field"):
        policy.append(store, data, notices=notices, reason="synthetic baseline")
    assert not store.keys("policy")


@pytest.mark.parametrize("field", ["calendar", "tax"])
@pytest.mark.parametrize("damage", ["no_source", "wrong_field", "wrong_effective_date"])
def test_every_2026_change_requires_field_and_effective_date_evidence(env, field, damage):
    import copy
    _, store, rules, _, _, _ = env
    data, notices = official_2026_policy(rules)
    policy.append(store, data, notices=notices, reason="synthetic baseline")
    changed = copy.deepcopy(data)
    if field == "calendar":
        changed["sessions"].remove("2026-12-25")
    else:
        changed["tax"].append({"from": "2026-12-10", "KOSPI": .003, "KOSDAQ": .003})
    source = next(s for s in changed["sources"] if s["kind"] == field)
    if damage == "no_source":
        changed["sources"].remove(source)
    elif damage == "wrong_field":
        source["fields"] = ["calendar_through" if field == "calendar" else "tax_through"]
    else:
        source["effective_through"] = "2026-12-09"
    with pytest.raises(ValueError, match="official evidence for changed field/effective date"):
        policy.append(store, changed, notices=notices, reason="invalid synthetic 2026 change")
    assert len(store.keys("policy")) == 1


def test_verified_2026_baseline_supports_november_activation_and_binds_changes(env):
    from backtesting.experiments.bb003 import recorder
    _, old_store, rules, _, clock, _ = env
    store = storage.Store(old_store.root.parent.parent / "november", clock=lambda: clock[0])
    data, notices = official_2026_policy(rules)
    clock[0] = storage.aware("2026-11-30T12:00:00+09:00")
    policy.append(store, data, notices=notices, reason="synthetic officially shaped 2026 baseline")
    baseline = policy.latest(store)
    assert baseline.entry_schedule("2026-12-01")["entry_date"] == "2026-12-02"
    with pytest.raises(policy.NotReady, match="calendar_unverified"):
        baseline.freeze()
    recorder.activate(store, commit="a" * 40, manifest_hash="b" * 64)
    assert recorder.activation_status(store) == "active"
    assert {r["field"] for r in store.get("policy", "000000")["changes"]} == {
        "calendar_start", "calendar_through", "sessions", "tax_through", "tax.KOSPI", "tax.KOSDAQ"}


@pytest.mark.parametrize("script", ["runtime", "activate", "bugfix"])
def test_deployment_manual_entrypoints_check_priority_before_environment_hash(script, monkeypatch, capsys):
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location("bb003_test_" + script, ROOT / f"deploy/collector-bb003/{script}.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    @contextmanager
    def blocked(**kwargs):
        raise policy.NotReady("FW001_shadow_morning_priority")
        yield
    monkeypatch.setattr(module, "operational_lock", blocked)
    monkeypatch.setattr(module, "verify_operation", lambda *a, **k: pytest.fail("priority must precede environment hashing"))
    if script == "runtime":
        assert module.main([]) == 0
        assert "FW001_shadow_morning_priority" in capsys.readouterr().out
    else:
        args = [] if script == "activate" else ["--before", "/unread-before", "--after", "/unread-after",
            "--commit", "a" * 40, "--reason", "test"]
        with pytest.raises(policy.NotReady, match="morning_priority"):
            module.main(args)


def test_activation_cli_on_november_30_needs_no_2027_exit_calendar(tmp_path, fake_publication, monkeypatch):
    from importlib.util import spec_from_file_location, module_from_spec
    from types import SimpleNamespace
    import pwd
    from backtesting.experiments.bb003 import recorder
    spec = spec_from_file_location("bb003_test_activation", ROOT / "deploy/collector-bb003/activate.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    clock, _ = fake_publication
    clock[0] = storage.aware("2026-11-30T12:00:00+09:00")
    store = storage.Store(tmp_path / "state", clock=lambda: clock[0])
    raw = storage.encoded({"commit": "a" * 40})
    store.test_manifest_sha256 = storage.sha(raw)
    storage.put(tmp_path / "code" / deployment.MANIFEST, raw)
    baseline = policy.shared_policy("2026-07-01", "2026-12-31")
    with pytest.raises(policy.NotReady, match="21st_session"):
        baseline.schedule("2026-12-01")
    monkeypatch.setattr(module, "ROOT", tmp_path / "code")
    monkeypatch.setattr(module, "Store", lambda *a: store)
    monkeypatch.setattr(module, "operational_lock", nullcontext)
    monkeypatch.setattr(module, "fw001_contract", lambda: {})
    monkeypatch.setattr(policy, "latest", lambda *a: baseline)
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(module.os, "chown", lambda *a: None)
    monkeypatch.setattr(pwd, "getpwnam", lambda *a: SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()))
    commands = []
    monkeypatch.setattr(module.subprocess, "check_call", lambda command: commands.append(command))
    assert module.main(["--execute"]) == 0
    assert recorder.activation_status(store) == "active"
    assert commands[-1] == ["systemctl", "enable", "--now", "hqa-bb003-daily.timer"]


def test_policy_evidence_can_cover_changed_field_with_consecutive_official_notices(env):
    _, store, rules, _, _, _ = env
    data, notices = official_2026_policy(rules)
    sources = []
    for source in data["sources"]:
        for first, last in (("2026-01-01", "2026-12-15"), ("2026-12-16", "2026-12-31")):
            sources.append({**source, "effective_from": first, "effective_through": last})
    data["sources"] = sources
    policy.append(store, data, notices=notices, reason="consecutive synthetic notice intervals")
    assert policy.latest(store).digest == policy.Policy(data).digest


def test_kernel_budget_kills_worker_and_releases_shared_lock(tmp_path):
    import signal
    lock = tmp_path / "run.lock"
    lock.write_bytes(b"")
    code = """import signal, sys
from backtesting.experiments.bb003 import deployment as d
d.RUN_BUDGET_SECONDS = 0.15
d.deadline_priority = lambda **k: None
original = d.heavy_lock
d.heavy_lock = lambda: original(sys.argv[1])
with d.operational_lock():
    print('locked', flush=True)
    signal.pause()
"""
    process = subprocess.run([sys.executable, "-B", "-c", code, str(lock)], cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHON_DOTENV_DISABLED": "1"},
        capture_output=True, text=True, timeout=5)
    assert process.returncode == -signal.SIGALRM and process.stdout.strip() == "locked"
    with deployment.heavy_lock(lock):
        pass


def admission_plan(env, day="2026-12-02"):
    source, store, rules, _, clock, _ = env
    clock[0] = storage.aware(day + "T08:03:00+09:00")
    plan = admission.entry_plan(rules, rules.sessions.get_loc(day))
    storage.put(store.path("morning_plan/" + day + ".json"), storage.encoded(plan))
    priority_calendar(store, rules)
    return plan


def priority_calendar(store, rules):
    if not store.keys("policy"):
        store.seal("policy", "000000", {"policy": rules.data})


def priority_marker(env, owner, plan):
    source, _, _, _, clock, _ = env
    month = plan[owner + "_month"]
    base = source / "forward" / owner
    candidate = base / "decisions" / (month.replace("-", "") + ".json")
    deadline = storage.aware(plan["entry_date"] + "T08:59:00+09:00")
    finalised = min(clock[0], deadline) - timedelta(hours=12)
    record = {"month": month, "trade_date": plan["entry_date"], "label": "forward",
              "deadline": plan["entry_date"] + "T08:59:00+09:00", "finalised_at": finalised.isoformat(),
              "S_t": finalised.isoformat(), "calendar_version": "synthetic",
              "experiment_id": "FW002_investor_flow_forward", "record_type": "decision"}
    raw = storage.encoded(record)
    storage.put(candidate, raw)
    if owner == "fw001":
        marker = base / "sealed" / (month.replace("-", "") + ".decision") / "complete.json"
        proof = {"record_path": candidate.relative_to(source).as_posix(), "record_digest": storage.sha(raw),
                 "publication_clock": "private_directory_mtime_ns", "publication_precision_ns": 1_000_000_000,
                 "reconstructed": False, "prepared_at": finalised.isoformat(), "deadline": record["deadline"]}
    else:
        marker = Path(str(candidate) + ".complete")
        proof = {"experiment_id": "FW002_investor_flow_forward", "record_type": "decision_completion",
                 "decision_hash": storage.sha(raw), "deadline": record["deadline"],
                 "calendar_version": "synthetic", "completed_at": min(clock[0], deadline - timedelta(minutes=1)).isoformat()}
    storage.put(marker, storage.encoded(proof))
    if owner == "fw001":
        ns = int(clock[0].timestamp() * 1e9)
        os.utime(marker.parent, ns=(ns, ns))
    return candidate, marker


def test_KRX_published_at_0800_seals_from_night_preparation_before_0859(env, monkeypatch, tmp_path):
    source, store, rules, client, clock, item = env
    last = source / "market/krx_daily/2026/20261201.jsonl"
    raw = last.read_bytes()
    last.unlink()
    clock[0] = storage.aware("2026-12-01T18:05:00+09:00")
    assert recorder.prepare_day(store, source, rules, client)["counts"]["prepared"] == 1
    priority_calendar(store, rules)
    prep_hash = store.proof("preparations", item["rcept_no"] + ".000000")["record_sha256"]
    assert not store.keys("decisions")
    assert client.calls == 1
    clock[0] = storage.aware("2026-12-02T08:00:00+09:00")
    storage.put(last, raw)
    # Everything except the just-published session must come from preparation.
    for path in last.parent.glob("*.jsonl"):
        if path != last:
            path.unlink()
    monkeypatch.setattr(recorder.Listings, "load", lambda *a: pytest.fail("morning cannot scan listings"))
    monkeypatch.setattr(recorder, "acquire", lambda *a: pytest.fail("morning cannot fetch DART"))
    monkeypatch.setattr(recorder.Prices, "prepare_entry", lambda *a: pytest.fail("morning cannot prepare history"))
    lock = tmp_path / "heavy.lock"
    lock.write_bytes(b"")
    original = deployment.heavy_lock
    monkeypatch.setattr(deployment, "heavy_lock", lambda: original(lock))
    manager = InstalledManager(clock)
    clock[0] = storage.aware("2026-12-02T08:03:00+09:00")
    with deployment.operational_lock(phase="seal", source=source, state=store.root, clock=lambda: clock[0], run=manager) as guard:
        store.morning_guard = guard
        assert recorder.seal_day(store, source, rules)["counts"]["included"] == 1
    proof = store.proof("decisions", item["rcept_no"])
    assert proof["valid"] and storage.aware(proof["publication_not_after"]) < storage.aware("2026-12-02T08:59:00+09:00")
    assert store.proof("preparations", item["rcept_no"] + ".000000")["record_sha256"] == prep_hash
    assert len(proof["record"]["input_hashes"]) >= 20


def test_month_end_waits_for_FW001_then_FW002_markers_and_idle_shadow(env):
    source, store, _, _, clock, _ = env
    plan = admission_plan(env, "2026-12-01")
    assert plan["fw001_month"] == plan["fw002_month"] == "2026-11"
    manager = InstalledManager(clock)
    check = lambda: admission.morning(source, store.root, clock=lambda: clock[0], run=manager, verify=True)
    with pytest.raises(policy.NotReady, match="fw001_decision_marker_missing"):
        check()
    priority_marker(env, "fw001", plan)
    manager.active = ["hqa-fw001-recovery.service loaded activating start running"]
    with pytest.raises(policy.NotReady, match="higher_priority_worker_busy"):
        check()
    manager.active = []
    with pytest.raises(policy.NotReady, match="fw002_decision_marker_missing"):
        check()
    priority_marker(env, "fw002", plan)
    manager.active = ["hqa-shadow-daily.service loaded active running"]
    with pytest.raises(policy.NotReady, match="higher_priority_worker_busy"):
        check()
    manager.active = []
    assert check() == plan


@pytest.mark.parametrize("owner", ["fw001", "fw002"])
@pytest.mark.parametrize("damage", ["wrong_entry", "unsealed", "bytes"])
def test_priority_marker_must_be_for_this_entry_and_completed(env, owner, damage):
    source, store, _, _, clock, _ = env
    plan = admission_plan(env, "2026-12-01")
    paths = {o: priority_marker(env, o, plan) for o in ("fw001", "fw002")}
    candidate, marker = paths[owner]
    if damage == "wrong_entry":
        record = storage.load(candidate)
        candidate.write_bytes(storage.encoded({**record, "trade_date": "2026-12-02"}))
    elif damage == "unsealed":
        marker.unlink()
    else:
        candidate.write_bytes(candidate.read_bytes() + b" ")
    with pytest.raises(ValueError):
        admission.morning(source, store.root, clock=lambda: clock[0], run=InstalledManager(clock), verify=True)


@pytest.mark.parametrize("unit", ["hqa-fw001-daily.service", "hqa-fw001-retry.service", "hqa-fw002-decision.service",
                                "hqa-fw002-check@decision.service", "hqa-shadow-daily.service", "hqa-krx-daily.service"])
@pytest.mark.parametrize("state", ["active", "activating"])
def test_no_seal_while_any_priority_worker_is_active_or_activating(env, unit, state):
    source, store, _, _, clock, _ = env
    admission_plan(env)
    manager = InstalledManager(clock)
    manager.active = [f"{unit} loaded {state} running"]
    with pytest.raises(policy.NotReady, match="higher_priority_worker_busy"):
        admission.morning(source, store.root, clock=lambda: clock[0], run=manager)


@pytest.mark.parametrize("trigger", ["08:02:00", "08:12:00", "08:25:00", "08:40:00"])
def test_all_four_KRX_FW001_triggers_have_zero_BB003_lock_delay(env, trigger):
    source, store, _, _, clock, _ = env
    admission_plan(env)
    manager, accepted = InstalledManager(clock), []
    due = storage.aware("2026-12-02T" + trigger + "+09:00")
    for minute in range(3, 56):
        clock[0] = storage.aware(f"2026-12-02T08:{minute:02d}:00+09:00")
        try:
            admission.morning(source, store.root, clock=lambda: clock[0], run=manager)
        except policy.NotReady:
            continue
        accepted.append(clock[0])
        end = clock[0] + timedelta(seconds=admission.SEAL_BUDGET_SECONDS + admission.SEAL_MARGIN_SECONDS)
        assert not clock[0] <= due <= end
    assert accepted  # Admission must leave a usable morning window.


def test_late_KRX_records_terminal_reason_and_never_buys_retroactively(env, monkeypatch):
    source, store, rules, client, clock, item = env
    last = source / "market/krx_daily/2026/20261201.jsonl"
    raw = last.read_bytes()
    last.unlink()
    clock[0] = storage.aware("2026-12-01T18:05:00+09:00")
    recorder.prepare_day(store, source, rules, client)
    priority_calendar(store, rules)
    clock[0] = storage.aware("2026-12-02T08:55:00+09:00")
    with monkeypatch.context() as denied:
        denied.setattr(deployment, "heavy_lock", lambda: pytest.fail("denied admission cannot take the lock"))
        denied.setattr(deployment, "verify_operation", lambda *a, **k: pytest.fail("denied admission cannot hash"))
        with pytest.raises(policy.NotReady, match="previous_session_KRX_missing"):
            with deployment.operational_lock(phase="seal", source=source, state=store.root, clock=lambda: clock[0], run=InstalledManager(clock)):
                pytest.fail("not admissible")
    reason = storage.load(store.path("morning_missed/2026-12-02.json"))["reason"]
    assert reason.startswith("previous_session_KRX_missing")
    clock[0] = storage.aware("2026-12-02T09:01:00+09:00")
    storage.put(last, raw)
    assert recorder.prepare_day(store, source, rules, client)["counts"]["data_not_ready"] == 1
    assert store.get("data_not_ready", item["rcept_no"])["reason"] == reason
    assert not store.keys("decisions") and client.calls == 1


@pytest.mark.parametrize("override", [{"LoadState": "not-found"}, {"NeedDaemonReload": "yes"},
    {"ActiveState": "inactive"}, {"TimersCalendar": ""}, {"AccuracyUSec": "infinity"},
    {"NextElapseUSecRealtime": "n/a"}])
def test_unknown_or_unbounded_installed_schedules_fail_closed(env, override):
    source, store, _, _, clock, _ = env
    admission_plan(env)
    manager = InstalledManager(clock)
    manager.overrides[admission.TIMERS[1]] = override
    with pytest.raises(ValueError):
        admission.morning(source, store.root, clock=lambda: clock[0], run=manager)


def test_morning_tracks_installed_schedule_changes_and_Persistent_catchup(env):
    source, store, _, _, clock, _ = env
    admission_plan(env)
    manager = InstalledManager(clock)
    assert admission.morning(source, store.root, clock=lambda: clock[0], run=manager)
    manager.times[admission.TIMERS[1]].append("08:04:00")
    with pytest.raises(policy.NotReady, match="trigger_within_seal_budget"):
        admission.morning(source, store.root, clock=lambda: clock[0], run=manager)
    manager.overrides[admission.TIMERS[1]] = {"NextElapseUSecRealtime": "2026-12-02T08:02:00+09:00", "LastTriggerUSec": "0"}
    with pytest.raises(policy.NotReady, match="pending_installed_timer_catchup"):
        admission.morning(source, store.root, clock=lambda: clock[0], run=manager)


@pytest.mark.parametrize("stamp,allowed", [("08:55:00", True), ("08:55:00.000001", False)])
def test_seal_runtime_and_margin_define_last_start(env, stamp, allowed):
    source, store, _, _, clock, _ = env
    admission_plan(env)
    clock[0] = storage.aware("2026-12-02T" + stamp + "+09:00")
    if allowed:
        assert admission.morning(source, store.root, clock=lambda: clock[0], run=InstalledManager(clock))
    else:
        with pytest.raises(policy.NotReady, match="runtime_cutoff"):
            admission.morning(source, store.root, clock=lambda: clock[0], run=InstalledManager(clock))


def test_queued_priority_job_cannot_be_hidden_by_inactive_service(env):
    source, store, _, _, clock, _ = env
    admission_plan(env)
    manager = InstalledManager(clock)
    manager.pending = ["47 hqa-fw001-daily.service start waiting"]
    with pytest.raises(policy.NotReady, match="higher_priority_worker_queued"):
        admission.morning(source, store.root, clock=lambda: clock[0], run=manager)


def test_absent_FW002_requires_both_timer_and_worker_absent(env):
    source, store, _, _, clock, _ = env
    plan = admission_plan(env, "2026-12-01")
    priority_marker(env, "fw001", plan)
    manager = InstalledManager(clock)
    manager.overrides[admission.FW002_TIMERS[0]] = {"LoadState": "not-found"}
    manager.overrides["hqa-fw002-decision.service"] = {"LoadState": "loaded"}
    with pytest.raises(ValueError, match="incomplete FW002"):
        admission.morning(source, store.root, clock=lambda: clock[0], run=manager)
    manager.overrides.update({u: {"LoadState": "not-found"} for u in (*admission.FW002_TIMERS, *admission.FW002_SERVICES)})
    assert admission.morning(source, store.root, clock=lambda: clock[0], run=manager)


@pytest.mark.parametrize("missing", ["KOSDAQ", "KOSPI", "truncated"])
def test_morning_requires_complete_both_market_KRX_file(env, missing):
    source, store, _, _, clock, _ = env
    admission_plan(env)
    path = source / "market/krx_daily/2026/20261201.jsonl"
    rows = path.read_bytes().splitlines()
    path.write_bytes(b"{\n" if missing == "truncated" else b"\n".join(r for r in rows if missing.encode() not in r))
    with pytest.raises(policy.NotReady, match="previous_session_KRX"):
        admission.morning(source, store.root, clock=lambda: clock[0], run=InstalledManager(clock))


def test_post_lock_admission_and_final_link_recheck_precede_completion(env, monkeypatch):
    source, store, rules, client, clock, item = env
    recorder.prepare_day(store, source, rules, client)
    priority_calendar(store, rules)
    manager = InstalledManager(clock)
    clock[0] = storage.aware("2026-12-02T08:03:00+09:00")
    @contextmanager
    def raced_lock():
        manager.pending = ["18 hqa-fw001-daily.service start waiting"]
        yield
    monkeypatch.setattr(deployment, "heavy_lock", raced_lock)
    with pytest.raises(policy.NotReady, match="queued"):
        with deployment.operational_lock(phase="seal", source=source, state=store.root, clock=lambda: clock[0], run=manager):
            pytest.fail("hashing must follow the second admission")
    assert not store.keys("decisions")
    manager.pending = []
    original = deployment.verify_operation
    def changed(store, **kwargs):
        result = original(store, **kwargs)
        manager.active = ["hqa-shadow-daily.service loaded active running"]
        return result
    monkeypatch.setattr(deployment, "verify_operation", changed)
    store.morning_guard = lambda: admission.morning(source, store.root, clock=lambda: clock[0], run=manager, verify=True)
    with pytest.raises(policy.NotReady, match="worker_busy"):
        recorder.seal_day(store, source, rules)
    assert not store.keys("decisions") and store.marker("decisions", item["rcept_no"]).exists() is False


def test_tampered_cheap_plan_cannot_bypass_official_priority_rules(env):
    source, store, rules, client, _, item = env
    recorder.prepare_day(store, source, rules, client)
    true_plan = recorder.entry_plan(rules, rules.entry(item["rcept_dt"]))
    store.morning_guard = lambda: {**true_plan, "previous_session": "2026-11-30"}
    with pytest.raises(ValueError, match="cheap admission plan differs"):
        recorder.seal_day(store, source, rules)
    assert not store.keys("decisions")


@pytest.mark.parametrize("field,value", [("previous_session", "2026-06-30"), ("fw001_month", "../../.env")])
def test_cheap_plan_cannot_request_protected_inputs_or_unsafe_paths(env, monkeypatch, field, value):
    source, store, _, _, clock, _ = env
    plan = admission_plan(env)
    path = store.path("morning_plan/2026-12-02.json")
    path.write_bytes(storage.encoded({**plan, field: value}))
    monkeypatch.setattr(admission, "installed_windows", lambda *a, **k: pytest.fail("invalid plan must fail before any further reads"))
    with pytest.raises(ValueError, match="unsafe or protected|unsafe priority"):
        admission.morning(source, store.root, clock=lambda: clock[0], run=InstalledManager(clock))


@pytest.mark.parametrize("damage", [None, "scope", "verified_worst_case", "host", "source_files", "environment_sha256",
                                    "worst_wall_seconds", "peak_rss_mib"])
def test_final_server_seal_runtime_evidence_is_bound_and_below_caps(damage):
    manifest = {"files": {"worker.py": "a" * 64}, "environment_sha256": "b" * 64}
    evidence = {"scope": "full_morning_seal_including_verification_and_lock_release", "verified_worst_case": True,
                "host": os.uname().nodename, "source_files": manifest["files"],
                "environment_sha256": manifest["environment_sha256"], "worst_wall_seconds": 179., "peak_rss_mib": 500.}
    if damage:
        evidence[damage] = {"scope": "rehearsal", "verified_worst_case": False, "host": "different-server",
            "source_files": {}, "environment_sha256": "c" * 64,
            "worst_wall_seconds": 180., "peak_rss_mib": 600.}[damage]
        with pytest.raises(ValueError, match="BB003"):
            deployment.verify_seal_runtime(storage.encoded(evidence), manifest)
    else:
        assert deployment.verify_seal_runtime(storage.encoded(evidence), manifest) == storage.sha(storage.encoded(evidence))


def fw002_fills(env, month="2026-11", entry="2026-12-01", count=3, source_ready=True):
    source, _, _, _, clock, _ = env
    candidate, _ = priority_marker(env, "fw002", {"fw002_month": month, "entry_date": entry})
    record = storage.load(candidate)
    for m in (1.0, 1.5, 2.0)[:count]:
        fill = source / f"forward/fw002/fills/{month.replace('-', '')}_{m}.json"
        if fill.exists():
            continue
        storage.put(fill, storage.encoded({
            "experiment_id": "FW002_investor_flow_forward", "record_type": "fill", "month": month,
            "trade_date": entry, "label": "forward", "cost_multiplier": m,
            "decision_hash": storage.sha(candidate.read_bytes()), "decision_calendar_version": record["calendar_version"],
            "recorded_at": max(clock[0], storage.aware(entry + "T18:00:00+09:00")).isoformat()}))
    if source_ready:
        for m in (1.0, 1.5, 2.0):
            storage.put(source / f"forward/fw001/sealed/{month.replace('-', '')}.execution.{m}/complete.json", b"{}\n")


@pytest.mark.parametrize("phase", ["morning", "night"])
@pytest.mark.parametrize("count", [0, 1, 2])
def test_each_unfinished_FW002_fill_month_blocks_both_phases(env, phase, count):
    source, store, _, _, clock, _ = env
    admission_plan(env)
    # Latest month's fills cannot hide an older unfinished month.
    fw002_fills(env)
    fw002_fills(env, month="2026-10", entry="2026-11-02", count=count)
    if phase == "night":
        clock[0] = storage.aware("2026-12-02T19:05:00+09:00")
    manager = InstalledManager(clock)
    check = (lambda: admission.morning(source, store.root, clock=lambda: clock[0], run=manager, verify=True)) if phase == "morning" else (
        lambda: deployment.deadline_priority(source=source, state=store.root, clock=lambda: clock[0], run=manager))
    with pytest.raises(policy.NotReady, match="fw002_fill_missing:2026-10"):
        check()
    fw002_fills(env, month="2026-10", entry="2026-11-02")
    check()


@pytest.mark.parametrize("damage", ["decision_hash", "cost_multiplier", "trade_date", "month", "label", "decision_calendar_version", "record_type", "recorded_at"])
def test_all_three_FW002_cost_fills_must_link_to_the_completed_decision(env, damage):
    source, store, _, _, clock, _ = env
    admission_plan(env)
    fw002_fills(env)
    path = source / "forward/fw002/fills/202611_1.5.json"
    row = storage.load(path)
    row[damage] = {"decision_hash": "f" * 64, "cost_multiplier": 2.0, "trade_date": "2026-12-02", "month": "2026-10",
                   "label": "reconstructed", "decision_calendar_version": "wrong", "record_type": "decision",
                   "recorded_at": "2026-11-01T18:00:00+09:00"}[damage]
    path.write_bytes(storage.encoded(row))
    with pytest.raises(ValueError, match="fill/decision linkage"):
        admission.morning(source, store.root, clock=lambda: clock[0], run=InstalledManager(clock), verify=True)


def test_FW002_unfinished_fill_is_not_due_until_all_FW001_execution_paths_exist(env):
    source, store, _, _, clock, _ = env
    admission_plan(env)
    fw002_fills(env, count=0, source_ready=False)
    manager = InstalledManager(clock)
    admission.morning(source, store.root, clock=lambda: clock[0], run=manager)
    for m in (1.0, 1.5):
        storage.put(source / f"forward/fw001/sealed/202611.execution.{m}/complete.json", b"{}\n")
    admission.morning(source, store.root, clock=lambda: clock[0], run=manager)
    storage.put(source / "forward/fw001/sealed/202611.execution.2.0/complete.json", b"{}\n")
    with pytest.raises(policy.NotReady, match="fw002_fill_missing"):
        admission.morning(source, store.root, clock=lambda: clock[0], run=manager)


def test_FW002_own_fill_never_uses_FW001_absence_to_skip_an_unfinished_month(env):
    source, store, rules, _, clock, _ = env
    clock[0] = storage.aware("2027-11-02T19:05:00+09:00")
    priority_calendar(store, rules)
    fw002_fills(env, month="2027-10", entry="2027-11-01", count=2, source_ready=False)
    with pytest.raises(policy.NotReady, match="fw002_fill_missing:2027-10:2.0"):
        deployment.deadline_priority(source=source, state=store.root, clock=lambda: clock[0], run=InstalledManager(clock))


@pytest.mark.parametrize("stamp,blocked", [("18:48:54", False), ("18:49:55", True), ("19:05:00", True)])
def test_night_FW002_decision_gate_includes_a_decision_due_within_the_run_budget(env, stamp, blocked):
    source, store, rules, _, clock, _ = env
    priority_calendar(store, rules)
    clock[0] = storage.aware("2026-11-30T" + stamp + "+09:00")
    check = lambda: deployment.deadline_priority(source=source, state=store.root, clock=lambda: clock[0], run=InstalledManager(clock))
    if blocked:
        with pytest.raises(policy.NotReady, match="fw002_decision_marker_missing"):
            check()
    else:
        check()
    priority_marker(env, "fw002", {"fw002_month": "2026-11", "entry_date": "2026-12-01"})
    check()  # Tomorrow's fills are explicitly not due yet.


@pytest.mark.parametrize("value", ["0", "n/a", "infinity"])
def test_monotonic_only_FW002_fill_timer_cannot_be_ignored(env, value):
    source, store, _, _, clock, _ = env
    admission_plan(env)
    manager = InstalledManager(clock)
    windows, installed = admission.installed_windows(clock[0], run=manager)
    assert admission.FW002_TIMERS[1] in installed
    assert any(unit == admission.FW002_TIMERS[1] for _, _, unit in windows)
    manager.overrides[admission.FW002_TIMERS[1]] = {"NextElapseUSecMonotonic": value}
    with pytest.raises(ValueError, match="monotonic timer"):
        admission.morning(source, store.root, clock=lambda: clock[0], run=manager)


@pytest.mark.parametrize("value", ["0", "n/a", "infinity", str(2**64 - 1)])
def test_exhausted_FW002_OnBoot_with_a_calendar_has_no_pending_monotonic_trigger(env, monkeypatch, value):
    source, store, _, _, clock, _ = env
    admission_plan(env)
    monkeypatch.setattr(admission.timer, "monotonic", lambda: 20 * 604800)
    manager = InstalledManager(clock)
    manager.overrides[admission.FW002_TIMERS[0]] = {
        "TimersMonotonic": "{ OnBootUSec=2min ; next_elapse=0 ; }", "NextElapseUSecMonotonic": value}
    admission.morning(source, store.root, clock=lambda: clock[0], run=manager)


@pytest.mark.parametrize("change", ["repeating", "unknown", "not_triggered", "not_elapsed"])
def test_absent_monotonic_next_is_accepted_only_for_an_elapsed_one_shot(env, monkeypatch, change):
    _, _, _, _, clock, _ = env
    manager = InstalledManager(clock)
    fields = {"TimersMonotonic": "{ OnBootUSec=2min ; }", "NextElapseUSecMonotonic": "0"}
    if change == "repeating":
        fields["TimersMonotonic"] += " { OnUnitInactiveUSec=10min ; }"
    elif change == "unknown":
        fields["TimersMonotonic"] = "unrecognized timer metadata"
    elif change == "not_triggered":
        fields["LastTriggerUSec"] = "0"
    monkeypatch.setattr(admission.timer, "monotonic", lambda: 90 if change == "not_elapsed" else 2000)
    manager.overrides[admission.FW002_TIMERS[0]] = fields
    with pytest.raises(ValueError, match="pending monotonic timer"):
        admission.installed_windows(clock[0], run=manager)


def test_systemctl_duration_parser_and_monotonic_window_support_weeks_and_days(env, monkeypatch):
    assert admission._seconds("2w 3d 4h 5min 6s 7ms 8us") == pytest.approx(1483506.007008)
    assert admission._seconds("1483506007008") == pytest.approx(1483506.007008)
    _, _, _, _, clock, _ = env
    manager = InstalledManager(clock)
    monkeypatch.setattr(admission.timer, "monotonic", lambda: 17 * 86400 + 3600)
    manager.overrides[admission.FW002_TIMERS[1]] = {"NextElapseUSecMonotonic": "2w 3d 1h 1min"}
    windows, _ = admission.installed_windows(clock[0], run=manager)
    window = next(w for w in windows if w[2] == admission.FW002_TIMERS[1])
    assert window[0] == clock[0] + timedelta(minutes=1)


@pytest.mark.parametrize("month", admission.FW002_MONTHS)
def test_all_36_registered_FW002_months_block_unsealed_fills_and_accept_complete_fills(env, month):
    source, _, _, _, clock, _ = env
    year, number = map(int, month.split("-"))
    entry = datetime(year + number // 12, number % 12 + 1, 1)
    while entry.weekday() >= 5:
        entry += timedelta(days=1)
    day = entry.date().isoformat()
    clock[0] = storage.aware(day + "T19:05:00+09:00")
    fw002_fills(env, month=month, entry=day, count=2, source_ready=month <= "2027-09")
    with pytest.raises(policy.NotReady, match="fw002_fill_missing:" + month + ":2.0"):
        admission.fw002_ready(source, None, clock[0], set(admission.FW002_TIMERS), verify=True)
    fw002_fills(env, month=month, entry=day, count=3, source_ready=month <= "2027-09")
    admission.fw002_ready(source, None, clock[0], set(admission.FW002_TIMERS), verify=True)
    plan = admission.entry_plan(type("Calendar", (), {"sessions": [entry - timedelta(days=entry.day), entry]})(), 1)
    assert plan["fw002_month"] == month
    assert len(admission.FW002_MONTHS) == 36 and admission.FW002_MONTHS[-1] == "2029-09"


@pytest.mark.parametrize("month", ["2028-10", "2029-09"])
def test_third_year_FW002_due_decisions_are_gated_through_the_last_registered_month(env, month):
    import pandas as pd
    source, store, rules, _, clock, _ = env
    extended = policy.Policy({**rules.data, "calendar_through": "2029-12-31",
                             "sessions": pd.bdate_range("2026-07-01", "2029-12-31").strftime("%Y-%m-%d").tolist()})
    priority_calendar(store, extended)
    days = [d for d in extended.data["sessions"] if d.startswith(month + "-")]
    entry = next(d for d in extended.data["sessions"] if d > days[-1])
    clock[0] = storage.aware(days[-1] + "T19:05:00+09:00")
    check = lambda: deployment.deadline_priority(source=source, state=store.root, clock=lambda: clock[0], run=InstalledManager(clock))
    with pytest.raises(policy.NotReady, match="fw002_decision_marker_missing"):
        check()
    priority_marker(env, "fw002", {"fw002_month": month, "entry_date": entry})
    check()


@pytest.mark.parametrize("month", ["202609", "202910", "202613"])
def test_FW002_out_of_registration_decisions_are_still_refused(tmp_path, month):
    storage.put(tmp_path / f"forward/fw002/decisions/{month}.json", b"{}\n")
    with pytest.raises(ValueError, match="unregistered FW002"):
        admission.fw002_ready(tmp_path, None, storage.aware("2029-10-02T19:05:00+09:00"), set(admission.FW002_TIMERS))


@pytest.mark.parametrize("stage", ["decision", "fill"])
@pytest.mark.parametrize("damage", [None, "Triggers", "Paths", "NeedDaemonReload"])
def test_FW002_path_triggers_are_inspected_including_fill_all_cost_scenarios(env, stage, damage):
    _, _, _, _, clock, _ = env
    manager = InstalledManager(clock)
    unit = f"hqa-fw002-{stage}@202611.path"
    paths = (["/var/lib/hqa/data/forward/fw001/sealed/202611.decision"] if stage == "decision" else
             [f"/var/lib/hqa/data/forward/fw001/sealed/202611.execution.{m}" for m in (1.0, 1.5, 2.0)])
    fields = {"LoadState": "loaded", "NeedDaemonReload": "no", "ActiveState": "active",
              "Triggers": f"hqa-fw002-check@{stage}.service", "Paths": " ".join("PathChanged=" + p for p in paths)}
    manager.paths[unit] = fields
    if damage:
        fields[damage] = "yes" if damage == "NeedDaemonReload" else "incorrect"
        with pytest.raises(ValueError, match="path trigger"):
            admission.installed_windows(clock[0], run=manager)
    else:
        assert unit in admission.installed_windows(clock[0], run=manager)[1]


@pytest.mark.parametrize("phase", ["seal", "prepare"])
@pytest.mark.parametrize("race", [False, True])
def test_root_dispatcher_admits_then_submits_only_job_mode_fail(env, monkeypatch, tmp_path, phase, race):
    from importlib.util import spec_from_file_location, module_from_spec
    from backtesting.experiments.bb003 import resources
    source, store, _, _, clock, _ = env
    admission_plan(env)
    if phase == "prepare":
        clock[0] = storage.aware("2026-12-02T19:05:00+09:00")
    spec = spec_from_file_location("bb003_dispatch_test", ROOT / "deploy/collector-bb003/runtime.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "DATA_ROOT", source)
    monkeypatch.setattr(module, "ROOT", tmp_path / "code")
    storage.put(module.ROOT / deployment.MANIFEST, b"{}\n")
    storage.put(store.path("host_memory_validation.json"), b"{}\n")
    manager, commands = InstalledManager(clock), []
    monkeypatch.setattr(resources, "host_limits", lambda **k: {})
    def checked(*a):
        if race:
            manager.pending = ["21 hqa-fw002-fill.service start waiting"]
    monkeypatch.setattr(resources, "verify_memory", checked)
    if race:
        with pytest.raises(policy.NotReady, match="queued"):
            module.dispatch(store, phase, run=manager, start=commands.append)
        assert commands == []
    else:
        assert module.dispatch(store, phase, run=manager, start=commands.append)["status"] == "worker_dispatched"
        assert commands == [["systemctl", "--no-block", "--job-mode=fail", "start", "hqa-bb003-daily.service"]]


@pytest.mark.parametrize("phase", ["seal", "prepare"])
@pytest.mark.parametrize("race", ["active_exec", "fill_becomes_due", "during_final_scan"])
def test_FW002_races_after_memory_validation_are_refused_before_worker_submission(env, monkeypatch, tmp_path, phase, race):
    from importlib.util import spec_from_file_location, module_from_spec
    from backtesting.experiments.bb003 import resources
    source, store, _, _, clock, _ = env
    admission_plan(env)
    fw002_fills(env, count=0, source_ready=False)
    if phase == "prepare":
        clock[0] = storage.aware("2026-12-02T19:05:00+09:00")
    manager, commands = InstalledManager(clock), []
    spec = spec_from_file_location("bb003_dispatch_races", ROOT / "deploy/collector-bb003/runtime.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "DATA_ROOT", source)
    monkeypatch.setattr(module, "ROOT", tmp_path / "code")
    storage.put(module.ROOT / deployment.MANIFEST, b"{}\n")
    storage.put(store.path("host_memory_validation.json"), b"{}\n")
    monkeypatch.setattr(resources, "host_limits", lambda **k: {})
    original_ready = admission.fw002_ready
    def ready(*args, **kwargs):
        result = original_ready(*args, **kwargs)
        manager.active = ["hqa-fw002-fill.service loaded active running"]
        return result
    def checked(*args):
        if race == "active_exec":
            manager.active = ["hqa-fw002-fill.service loaded active running"]
        elif race == "fill_becomes_due":
            for multiplier in (1.0, 1.5, 2.0):
                storage.put(source / f"forward/fw001/sealed/202611.execution.{multiplier}/complete.json", b"{}\n")
        else:
            monkeypatch.setattr(admission, "fw002_ready", ready)
    monkeypatch.setattr(resources, "verify_memory", checked)
    with pytest.raises(policy.NotReady, match="fw002_fill_missing" if race == "fill_becomes_due" else "worker_busy"):
        module.dispatch(store, phase, run=manager, start=commands.append)
    assert not commands


@pytest.mark.parametrize("phase", ["seal", "prepare"])
@pytest.mark.parametrize("race", ["active_exec", "fill_becomes_due"])
def test_FW002_change_after_candidate_write_is_rechecked_before_completion_link(env, monkeypatch, phase, race):
    source, store, rules, client, clock, item = env
    recorder.prepare_day(store, source, rules, client)
    admission_plan(env)
    fw002_fills(env, count=0, source_ready=False)
    if phase == "prepare":
        clock[0] = storage.aware("2026-12-02T19:05:00+09:00")
    manager = InstalledManager(clock)
    monkeypatch.setattr(deployment, "heavy_lock", nullcontext)
    original = deployment.verify_operation
    kind, key = ("decisions", item["rcept_no"]) if phase == "seal" else ("samples", "night")
    def changed(store, **kwargs):
        result = original(store, **kwargs)
        if store.path("records/" + kind).exists():
            if race == "active_exec":
                manager.active = ["hqa-fw002-fill.service loaded active running"]
            else:
                for multiplier in (1.0, 1.5, 2.0):
                    storage.put(source / f"forward/fw001/sealed/202611.execution.{multiplier}/complete.json", b"{}\n")
        return result
    with deployment.operational_lock(phase=phase, source=source, state=store.root, clock=lambda: clock[0], run=manager) as guard:
        store.morning_guard = store.priority_guard = guard
        monkeypatch.setattr(deployment, "verify_operation", changed)
        with pytest.raises(policy.NotReady, match="fw002_fill_missing" if race == "fill_becomes_due" else "worker_busy"):
            if phase == "seal":
                recorder.seal_day(store, source, rules)
            else:
                store.seal(kind, key, {"input_hashes": {}})
    assert not store.proof(kind, key)
    assert list(store.path("records/" + kind).glob("*/*.json"))


def test_root_dispatcher_missed_audit_is_owned_and_readable_by_hqa_before_publication(tmp_path, monkeypatch):
    import pwd
    import stat
    from types import SimpleNamespace
    worker_uid, worker_gid = 2001, 2002
    ownership = {}
    state = tmp_path / "forward/bb003"
    target = state / "morning_missed/2026-12-02.json"
    stamp = storage.aware("2026-12-02T08:56:00+09:00")
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=worker_uid, pw_gid=worker_gid))
    monkeypatch.setattr(os, "chown", lambda path, uid, gid: ownership.__setitem__(Path(path).stat().st_ino, (uid, gid)))
    monkeypatch.setattr(os, "fchown", lambda fd, uid, gid: ownership.__setitem__(os.fstat(fd).st_ino, (uid, gid)))
    original_link, original_read = os.link, Path.read_bytes
    def readable(path, directory=False):
        info = Path(path).stat()
        assert ownership.get(info.st_ino, (0, 0)) == (worker_uid, worker_gid)
        mode = stat.S_IMODE(info.st_mode)
        assert mode == (0o750 if directory else 0o640)
        # Simulate access as hqa, without requiring actual root or a host hqa.
        assert mode & stat.S_IRUSR and (not directory or mode & stat.S_IXUSR)
        assert mode & stat.S_IRGRP and not mode & stat.S_IROTH
    def publish(source, destination):
        readable(source)
        for directory in (state.parent, state, target.parent):
            readable(directory, directory=True)
        assert destination == target
        return original_link(source, destination)
    def worker_read(path):
        if path == target:
            readable(path)
        return original_read(path)
    monkeypatch.setattr(os, "link", publish)
    monkeypatch.setattr(Path, "read_bytes", worker_read)
    previous = os.umask(0o077)
    try:
        admission.missed(state, "FW002 still due", clock=lambda: stamp)
    finally:
        os.umask(previous)
    assert storage.load(target)["reason"] == "FW002 still due"
    before = target.read_bytes()
    admission.missed(state, "different retry reason", clock=lambda: stamp)
    assert target.read_bytes() == before
    assert not list(target.parent.glob(".bb003-*"))


def test_mkstemp_candidates_and_proofs_are_group_readable_before_their_links(tmp_path, monkeypatch):
    import stat
    store = storage.Store(tmp_path)
    original = os.link
    modes = []
    def publish(source, destination):
        modes.append(stat.S_IMODE(Path(source).stat().st_mode))
        assert modes[-1] == 0o640
        return original(source, destination)
    monkeypatch.setattr(os, "link", publish)
    assert store.seal("samples", "readable", {"input_hashes": {}})["valid"]
    assert modes == [0o640, 0o640]


class StartTransactions:
    """Model only Conflicts, ordering and pending jobs from local systemd manpages.

    oneshot's start job lives through ExecStart; exec's ends at execve. A fail
    transaction cannot reverse a pending start into a stop. replace can. Stop
    precedes start for conflicts with either After or Before ordering.
    """
    def __init__(self, worker_text):
        self.worker = "hqa-bb003-daily.service"
        fields = dict(line.split("=", 1) for line in worker_text.splitlines() if "=" in line)
        self.conflicts, self.after = set(fields["Conflicts"].split()), set(fields["After"].split())
        self.pending, self.running, self.events = {}, set(), []

    def start(self, unit, *, mode="replace"):
        peers = self.conflicts if unit == self.worker else ({self.worker} if unit in self.conflicts else set())
        stops = peers.intersection(self.running | self.pending.keys())
        if mode == "fail" and any(self.pending.get(peer) == "start" for peer in stops):
            raise ValueError("pending start cannot be reversed by job-mode=fail")
        for peer in sorted(stops):
            assert (peer if unit == self.worker else unit) in self.after
            self.pending.pop(peer, None)
            self.running.discard(peer)
            self.events.append(("stop", peer))
        self.running.add(unit)
        self.events.append(("start", unit))
        if unit.startswith("hqa-fw002-"):
            self.pending.pop(unit, None)  # Type=exec start job completed.
        else:
            self.pending[unit] = "start"  # Type=oneshot ExecStart still running.


@pytest.mark.parametrize("priority", ["hqa-fw001-daily.service", "hqa-shadow-daily.service"])
def test_oneshot_pending_start_denies_BB003_fail_start_and_priority_replace_preempts_BB003(priority):
    text = (ROOT / "deploy/collector-bb003/systemd/hqa-bb003-daily.service").read_text()
    jobs = StartTransactions(text)
    jobs.start(priority)
    with pytest.raises(ValueError, match="pending start"):
        jobs.start(jobs.worker, mode="fail")
    assert jobs.running == {priority} and jobs.events == [("start", priority)]
    jobs = StartTransactions(text)
    jobs.start(jobs.worker, mode="fail")
    jobs.start(priority)  # Timer/default replace may cancel BB003's oneshot job.
    assert jobs.events[-2:] == [("stop", jobs.worker), ("start", priority)]
    assert jobs.running == {priority}


@pytest.mark.parametrize("fw002_first", [True, False])
@pytest.mark.parametrize("unit", ["hqa-fw002-decision.service", "hqa-fw002-fill.service"])
def test_FW002_exec_race_after_last_admission_can_run_concurrently_without_job_rejection(unit, fw002_first):
    jobs = StartTransactions((ROOT / "deploy/collector-bb003/systemd/hqa-bb003-daily.service").read_text())
    first, second = (unit, jobs.worker) if fw002_first else (jobs.worker, unit)
    jobs.start(first, mode="fail")
    jobs.start(second, mode="fail")
    assert jobs.running == {unit, jobs.worker}
    assert jobs.events == [("start", first), ("start", second)]
    assert jobs.pending == {jobs.worker: "start"}


@pytest.mark.parametrize("pid_matches", [True, False])
def test_runtime_worker_cannot_run_outside_its_conflicting_systemd_cgroup(pid_matches):
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location("bb003_worker_unit_test", ROOT / "deploy/collector-bb003/runtime.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    run = lambda *a, **k: "LoadState=loaded\nNeedDaemonReload=no\nMainPID=" + str(os.getpid() if pid_matches else 0)
    if pid_matches:
        module.require_worker_unit(run=run)
    else:
        with pytest.raises(ValueError, match="conflicting systemd unit"):
            module.require_worker_unit(run=run)


@pytest.mark.parametrize("command", ["record", "post-entry", "capture"])
def test_installed_manual_operational_CLI_names_dispatcher_without_running_work(monkeypatch, command):
    from scripts.bb003.__main__ import main
    monkeypatch.setattr(deployment, "CODE_ROOT", ROOT)
    monkeypatch.setattr(deployment, "operational_lock", lambda **k: pytest.fail("manual installed work cannot bypass the dispatcher"))
    with pytest.raises(ValueError, match="root admission dispatcher"):
        main([command, "--execute"])


@pytest.mark.parametrize("priority", ["hqa-fw001-daily.service", "hqa-shadow-daily.service"])
@pytest.mark.parametrize("stage", ["candidate_link", "proof_link", "after_proof_link"])
@pytest.mark.parametrize("retry", ["08:50:00", "08:56:00"])
def test_priority_start_stops_real_sealing_process_and_retry_never_accepts_a_partial_record(env, monkeypatch, tmp_path, priority, stage, retry):
    """Emulate the ordered systemd conflict transaction with a real SIGTERM.

    No host manager/units are changed. Unit contracts are checked separately;
    this regression exercises actual process death, locks and atomic storage.
    """
    import select
    import signal
    source, store, rules, client, clock, item = env
    clock[0] = storage.aware("2026-12-01T18:05:00+09:00")
    recorder.prepare_day(store, source, rules, client)
    admission_plan(env)
    preparation = store.proof("preparations", item["rcept_no"] + ".000000")["record_sha256"]
    marker = store.marker("decisions", item["rcept_no"])
    lock = tmp_path / "heavy.lock"
    lock.write_bytes(b"")
    original_lock = deployment.heavy_lock
    monkeypatch.setattr(deployment, "heavy_lock", lambda: original_lock(lock))
    manager = InstalledManager(clock)
    reader, writer = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(reader)
        try:
            original_link = storage.os.link
            def interrupted_link(candidate, target):
                target = Path(target)
                point = ((stage == "candidate_link" and target.is_relative_to(store.path("records/decisions")))
                         or (stage in {"proof_link", "after_proof_link"} and target == marker))
                if point:
                    if stage == "after_proof_link":
                        original_link(candidate, target)
                    os.write(writer, b"ready")
                    signal.pause()
                    raise AssertionError("worker must be stopped by priority start")
                return original_link(candidate, target)
            storage.os.link = interrupted_link
            with deployment.operational_lock(phase="seal", source=source, state=store.root, clock=lambda: clock[0], run=manager) as guard:
                store.morning_guard = guard
                with store.lock():
                    recorder.seal_day(store, source, rules)
            os._exit(2)
        except BaseException:
            import traceback
            traceback.print_exc()
            os._exit(3)
    os.close(writer)
    stopped = False
    try:
        assert select.select([reader], [], [], 10)[0], "worker never reached the chosen seal point"
        assert os.read(reader, 5) == b"ready"
        # systemd Conflicts + After stops the entire BB003 cgroup before
        # entering the higher-priority ExecStart. SIGTERM is real here.
        os.kill(pid, signal.SIGTERM)
        _, status = os.waitpid(pid, 0)
        stopped = True
        assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGTERM
        manager.active = [f"{priority} loaded active running"]
        with original_lock(lock):
            pass  # Stop released the shared inode; priority never waits on it.
        with pytest.raises(policy.NotReady, match="higher_priority_worker_busy"):
            admission.morning(source, store.root, clock=lambda: clock[0], run=manager)
        proof = store.proof("decisions", item["rcept_no"])
        assert bool(proof) is (stage == "after_proof_link")
        if proof:
            assert proof["valid"] and storage.load(store.path(proof["record_path"])) == proof["record"]
        else:
            assert not marker.exists()
        # Every published candidate is a complete JSON document; orphaned
        # temporary files never occur inside a completion-marker directory.
        for path in store.path("records/decisions").glob("*/*.json"):
            assert storage.load(path)["record_key"] == item["rcept_no"]
        assert not marker.parent.exists() or {p.name for p in marker.parent.iterdir()} <= {"complete.json"}
        manager.active = []
        clock[0] = storage.aware("2026-12-02T" + retry + "+09:00")
        if retry == "08:50:00":
            with deployment.operational_lock(phase="seal", source=source, state=store.root, clock=lambda: clock[0], run=manager) as guard:
                store.morning_guard = guard
                with store.lock():
                    result = recorder.seal_day(store, source, rules)
            final = store.proof("decisions", item["rcept_no"])
            assert final["valid"] and storage.aware(final["publication_not_after"]) < storage.aware("2026-12-02T08:59:00+09:00")
            assert result["counts"].get("already_sealed", 0) == int(proof is not None)
        else:
            with pytest.raises(policy.NotReady, match="runtime_cutoff"):
                with deployment.operational_lock(phase="seal", source=source, state=store.root, clock=lambda: clock[0], run=manager):
                    pytest.fail("late worker cannot seal")
            clock[0] = storage.aware("2026-12-02T09:01:00+09:00")
            recorder.prepare_day(store, source, rules, client)
            if proof:
                assert store.proof("decisions", item["rcept_no"])["record_sha256"] == proof["record_sha256"]
            else:
                missed = store.get("data_not_ready", item["rcept_no"])
                assert missed["record_kind"] == "data_not_ready" and missed["reason"].startswith("morning_seal_runtime_cutoff")
                assert storage.load(store.path("morning_missed/2026-12-02.json"))["status"] == "data_not_ready"
                assert not store.keys("decisions")
        assert store.proof("preparations", item["rcept_no"] + ".000000")["record_sha256"] == preparation
    finally:
        os.close(reader)
        if not stopped:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.waitpid(pid, 0)
