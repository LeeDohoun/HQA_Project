"""Synthetic final-host RAM/cgroup contracts and sealed memory admission."""
from copy import deepcopy
import os
from pathlib import Path

import pytest

from backtesting.experiments.bb003 import admission, deployment, recorder, resources, storage
from test_bb003_recorder import fake_publication, operation_verification

MIB = 2**20
ROOT = Path(__file__).resolve().parents[1]


class MemoryHost:
    def __init__(self, ram=2048, root_cap="max", slice_cap="infinity"):
        self.files = {"/proc/meminfo": f"MemTotal: {ram * 1024} kB\n", "/sys/fs/cgroup/memory.max": root_cap,
                      "/proc/self/mountinfo": "29 23 0:26 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n",
                      "/sys/fs/cgroup/system.slice/memory.max": "max" if slice_cap == "infinity" else slice_cap}
        self.fields = {"system.slice": {"LoadState": "loaded", "NeedDaemonReload": "no",
                                      "ControlGroup": "/system.slice",
                                      "MemoryMax": slice_cap, "EffectiveMemoryMax": slice_cap},
                       "hqa-bb003-daily.timer": {"LoadState": "loaded", "NeedDaemonReload": "no",
                                                "Triggers": "hqa-bb003-check.service"}}
        for unit in (*resources.PRIORITY_WORKERS, *resources.WORKERS, *resources.DISPATCHERS):
            dispatcher = unit in resources.DISPATCHERS
            cap = str((64 if unit == resources.DISPATCHERS[0] else 600) * MIB)
            self.fields[unit] = {"LoadState": "loaded", "NeedDaemonReload": "no", "Slice": "system.slice", "ControlGroup": "",
                "MemoryMax": cap, "EffectiveMemoryMax": cap, "MemoryAccounting": "yes",
                "Conflicts": " ".join(resources.CONFLICT_WORKERS) if unit in resources.WORKERS else "",
                "After": " ".join(resources.CONFLICT_WORKERS) if unit in resources.WORKERS else "",
                "KillMode": "control-group", "TimeoutStopUSec": "5s", "RemainAfterExit": "no",
                "Type": "exec" if unit in resources.FW002_WORKERS else "oneshot",
                "User": "root" if dispatcher else "hqa", "Group": "root" if dispatcher else "hqa"}

    def run(self, command, **kwargs):
        return "\n".join(k + "=" + v for k, v in self.fields[command[2]].items())

    def limits(self):
        def read(path):
            if path not in self.files:
                raise FileNotFoundError(path)
            if isinstance(self.files[path], Exception):
                raise self.files[path]
            return self.files[path]
        return resources.host_limits(run=self.run, read=read)


def memory_evidence(limits):
    manifest = {"files": {"worker.py": "a" * 64}, "environment_sha256": "b" * 64}
    evidence = {"scope": "bb003_plus_one_fw002_worker_dispatchers_and_resident_services", "verified_worst_case": True,
        "peak_metric": "cgroup_memory_peak_including_children_and_cache",
        "resident_scope": "all_other_services_and_kernel_including_permitted_priority_overlap",
        "host": limits["host"], "limits": limits, "source_files": manifest["files"],
        "environment_sha256": manifest["environment_sha256"],
        "heavy_job_peak_bytes": {u: 500 * MIB for u in (*resources.PRIORITY_WORKERS, *resources.WORKERS) if u in limits["units"]},
        "dispatcher_peak_bytes": {u: (30 if u == resources.DISPATCHERS[0] else 100) * MIB
                                  for u in resources.DISPATCHERS if u in limits["units"]},
        # Includes OS/kernel and every other permitted concurrent service.
        "resident_services_peak_bytes": 250 * MIB}
    return manifest, evidence


def test_every_BB003_worker_has_ordered_Conflicts_and_timer_uses_root_dispatcher():
    folder = ROOT / "deploy/collector-bb003/systemd"
    workers = []
    for path in folder.glob("*.service"):
        text = path.read_text()
        fields = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        if fields["User"] == "hqa":
            workers.append(path.name)
            assert set(resources.CONFLICT_WORKERS) == set(fields["Conflicts"].split())
            assert set(resources.CONFLICT_WORKERS) <= set(fields["After"].split())
            assert not set(admission.FW002_SERVICES).intersection(fields["Conflicts"].split())
            assert fields["KillMode"] == "control-group" and fields["TimeoutStopSec"] == "5"
        else:
            assert path.name == resources.DISPATCHERS[0]
            assert fields["User"] == fields["Group"] == "root" and "Conflicts" not in fields
            assert fields["ExecStart"].endswith("runtime.py trigger") and fields["MemoryMax"] == "64M"
    assert set(workers) == set(resources.WORKERS)
    assert "Unit=hqa-bb003-check.service" in (folder / "hqa-bb003-daily.timer").read_text()


@pytest.mark.parametrize("ram,root_cap,slice_cap,expected", [
    (961, "max", "infinity", 961), (2048, str(900 * MIB), "infinity", 900),
    (2048, "max", str(800 * MIB), 800), (700, str(900 * MIB), str(800 * MIB), 700)])
def test_effective_RAM_includes_root_cgroup_and_inherited_slice_caps(ram, root_cap, slice_cap, expected):
    limits = MemoryHost(ram, root_cap, slice_cap).limits()
    assert limits["effective_bytes"] == expected * MIB


def test_real_cgroup_root_without_memory_max_is_unlimited_and_RAM_still_bounds_fit():
    host = MemoryHost(ram=961)
    del host.files["/sys/fs/cgroup/memory.max"]
    limits = host.limits()
    assert limits["root_cgroup_memory_max_bytes"] is None
    assert limits["effective_bytes"] == 961 * MIB
    manifest, evidence = memory_evidence(limits)
    with pytest.raises(ValueError, match="activation refused"):
        resources.verify_memory(storage.encoded(evidence), manifest, limits)


@pytest.mark.parametrize("location,error,match", [
    ("/sys/fs/cgroup/system.slice/memory.max", FileNotFoundError(), "missing non-root"),
    ("/sys/fs/cgroup/memory.max", PermissionError(), "cannot read cgroup"),
    ("/sys/fs/cgroup/system.slice/memory.max", OSError("io failure"), "cannot read cgroup"),
    ("/sys/fs/cgroup/memory.max", "garbage", "invalid host memory"),
    ("/proc/self/mountinfo", "", "unambiguous cgroup"),
    ("/proc/self/mountinfo", "29 23 0:26 / /sys/fs/cgroup rw - cgroup cgroup rw\n", "unambiguous cgroup")])
def test_missing_child_limits_read_errors_and_wrong_hierarchy_fail_clearly(location, error, match):
    host = MemoryHost()
    host.files[location] = error
    with pytest.raises(ValueError, match=match):
        host.limits()


def test_cgroup_mount_is_discovered_and_nested_ancestor_and_unit_caps_apply():
    host = MemoryHost()
    host.files = {p.replace("/sys/fs/cgroup", "/run/cgroup-v2"): v for p, v in host.files.items()}
    host.files["/proc/self/mountinfo"] = "29 23 0:26 / /run/cgroup-v2 rw - cgroup2 cgroup rw\n"
    host.files["/run/cgroup-v2/team.slice/memory.max"] = str(1800 * MIB)
    host.files["/run/cgroup-v2/team.slice/team-jobs.slice/memory.max"] = "max"
    host.fields["team-jobs.slice"] = {**host.fields["system.slice"], "ControlGroup": "/team.slice/team-jobs.slice"}
    unit = resources.WORKERS[0]
    host.fields[unit].update(Slice="team-jobs.slice", ControlGroup="/team.slice/team-jobs.slice/" + unit)
    host.files["/run/cgroup-v2/team.slice/team-jobs.slice/" + unit + "/memory.max"] = str(550 * MIB)
    limits = host.limits()
    assert limits["effective_bytes"] == 1800 * MIB
    assert limits["units"][unit]["cgroup_effective_bytes"] == 550 * MIB
    assert limits["cgroup_hierarchy"]["limits"]["/team.slice"] == 1800 * MIB
    manifest, evidence = memory_evidence(limits)
    evidence["heavy_job_peak_bytes"][unit] = 560 * MIB
    with pytest.raises(ValueError, match="effective cgroup cap"):
        resources.verify_memory(storage.encoded(evidence), manifest, limits)


def test_FW002_template_dispatchers_can_use_their_systemd_escaped_default_slice():
    host = MemoryHost()
    # Instance services default to a per-template slice, with literal escaped
    # hyphens in the unit/cgroup names. FW002 check@ has no explicit Slice=.
    name = r"system-hqa\x2dfw002\x2dcheck.slice"
    group = "/system.slice/" + name
    host.fields[name] = {**host.fields["system.slice"], "ControlGroup": group}
    host.files["/sys/fs/cgroup" + group + "/memory.max"] = "max"
    for unit in resources.DISPATCHERS[1:]:
        host.fields[unit]["Slice"] = name
    limits = host.limits()
    for unit in resources.DISPATCHERS[1:]:
        assert limits["units"][unit]["ControlGroup"] == group + "/" + unit
    manifest, evidence = memory_evidence(limits)
    assert resources.verify_memory(storage.encoded(evidence), manifest, limits)["host_memory_budget"]["required_bytes"] == 1680 * MIB


def test_inactive_and_active_unit_cgroups_have_the_same_stable_limits_snapshot():
    host = MemoryHost()
    inactive = host.limits()
    for unit in (*resources.PRIORITY_WORKERS, *resources.WORKERS, *resources.DISPATCHERS):
        group = "/system.slice/" + unit
        host.fields[unit]["ControlGroup"] = group
        host.files["/sys/fs/cgroup" + group + "/memory.max"] = host.fields[unit]["MemoryMax"]
    assert host.limits() == inactive


@pytest.mark.parametrize("error", [FileNotFoundError(), PermissionError()])
def test_active_unit_limit_cannot_be_hidden_as_an_inactive_cgroup(error):
    host = MemoryHost()
    unit = resources.WORKERS[0]
    host.fields[unit]["ControlGroup"] = "/system.slice/" + unit
    host.files["/sys/fs/cgroup/system.slice/" + unit + "/memory.max"] = error
    with pytest.raises(ValueError, match="unit cgroup memory.max"):
        host.limits()


def test_delegated_mount_root_is_not_mistaken_for_the_unlimited_real_hierarchy_root():
    host = MemoryHost()
    host.files["/proc/self/mountinfo"] = "29 23 0:26 /tenant /sys/fs/cgroup rw - cgroup2 cgroup rw\n"
    host.fields["system.slice"]["ControlGroup"] = "/tenant/system.slice"
    host.files["/sys/fs/cgroup/memory.max"] = str(1700 * MIB)
    assert host.limits()["effective_bytes"] == 1700 * MIB
    del host.files["/sys/fs/cgroup/memory.max"]
    with pytest.raises(ValueError, match="missing non-root"):
        host.limits()


def test_absent_FW002_needs_no_concurrent_FW002_reservation():
    host = MemoryHost(ram=961)
    for unit in admission.FW002_SERVICES:
        host.fields[unit] = {"LoadState": "not-found"}
    limits = host.limits()
    manifest, evidence = memory_evidence(limits)
    budget = resources.verify_memory(storage.encoded(evidence), manifest, limits)["host_memory_budget"]
    assert budget["required_bytes"] == 880 * MIB


@pytest.mark.parametrize("unit,field,value", [
    (resources.WORKERS[0], "Conflicts", "hqa-fw001-daily.service"),
    (resources.WORKERS[0], "After", "hqa-fw001-daily.service"),
    (resources.WORKERS[0], "KillMode", "process"),
    (resources.WORKERS[0], "TimeoutStopUSec", "6s"),
    (resources.WORKERS[0], "MemoryMax", str(601 * MIB)),
    (resources.WORKERS[0], "Conflicts", " ".join((*resources.CONFLICT_WORKERS, resources.FW002_WORKERS[0]))),
    (resources.CONFLICT_WORKERS[0], "Type", "exec"),
    (resources.CONFLICT_WORKERS[1], "RemainAfterExit", "yes"),
    (resources.FW002_WORKERS[0], "Type", "oneshot"),
    (resources.DISPATCHERS[0], "User", "hqa"),
    (resources.DISPATCHERS[0], "Conflicts", resources.PRIORITY_WORKERS[0]),
    (resources.DISPATCHERS[0], "MemoryMax", str(600 * MIB)),
    ("hqa-bb003-daily.timer", "Triggers", resources.WORKERS[0])])
def test_activation_refuses_effective_units_that_can_break_serialization(unit, field, value):
    host = MemoryHost()
    host.fields[unit][field] = value
    with pytest.raises(ValueError, match="BB003"):
        host.limits()


@pytest.mark.parametrize("peak", [200, 400, 500])
def test_961_MiB_host_is_refused_with_concurrent_FW002_even_with_small_measured_peaks(peak):
    limits = MemoryHost(ram=961).limits()
    manifest, evidence = memory_evidence(limits)
    evidence["heavy_job_peak_bytes"] = {u: peak * MIB for u in evidence["heavy_job_peak_bytes"]}
    with pytest.raises(ValueError, match="activation refused: concurrent BB003"):
        resources.verify_memory(storage.encoded(evidence), manifest, limits)


def test_memory_reserves_BB003_and_largest_single_FW002_worker_plus_all_dispatchers_and_residents():
    limits = MemoryHost().limits()
    manifest, evidence = memory_evidence(limits)
    evidence["heavy_job_peak_bytes"][resources.FW002_WORKERS[1]] = 550 * MIB
    budget = resources.verify_memory(storage.encoded(evidence), manifest, limits)["host_memory_budget"]
    assert budget["bb003_plus_fw002_peak_bytes"] == 1050 * MIB
    assert budget["bb003_plus_fw002_reserved_bytes"] == 1200 * MIB
    assert budget["all_dispatchers_peak_bytes"] == 230 * MIB
    assert budget["required_bytes"] == 1680 * MIB


@pytest.mark.parametrize("damage", ["scope", "peak_metric", "resident_scope", "verified_worst_case", "host", "source_files", "environment_sha256", "limits",
    "missing_worker", "missing_dispatcher", "cap", "boolean", "resident_zero", "exact_limit"])
def test_memory_evidence_is_complete_bound_and_measured_below_limits(damage):
    limits = MemoryHost().limits()
    manifest, evidence = memory_evidence(limits)
    if damage in {"scope", "peak_metric", "resident_scope", "host", "environment_sha256"}:
        evidence[damage] = "wrong"
    elif damage == "verified_worst_case":
        evidence[damage] = False
    elif damage in {"limits", "source_files"}:
        evidence[damage] = {}
    elif damage == "missing_worker":
        evidence["heavy_job_peak_bytes"].pop(resources.PRIORITY_WORKERS[-1])
    elif damage == "missing_dispatcher":
        evidence["dispatcher_peak_bytes"].pop(resources.DISPATCHERS[-1])
    elif damage in {"cap", "boolean"}:
        evidence["heavy_job_peak_bytes"][resources.WORKERS[0]] = 600 * MIB if damage == "cap" else True
    elif damage == "resident_zero":
        evidence["resident_services_peak_bytes"] = 0
    else:
        evidence["resident_services_peak_bytes"] = limits["effective_bytes"] - 1430 * MIB
    with pytest.raises(ValueError, match="BB003"):
        resources.verify_memory(storage.encoded(evidence), manifest, limits)


def test_changed_limits_refuse_stale_evidence_and_per_unit_effective_cap_is_checked():
    limits = MemoryHost().limits()
    manifest, evidence = memory_evidence(limits)
    changed = deepcopy(limits)
    changed["root_cgroup_memory_max_bytes"] = changed["effective_bytes"] = 800 * MIB
    with pytest.raises(ValueError, match="current effective"):
        resources.verify_memory(storage.encoded(evidence), manifest, changed)
    limits["units"][resources.WORKERS[0]]["EffectiveMemoryMax"] = str(450 * MIB)
    with pytest.raises(ValueError, match="effective cgroup cap"):
        resources.verify_memory(storage.encoded(evidence), manifest, limits)


def test_activation_seals_effective_host_limits_measurement_and_budget(tmp_path, monkeypatch, fake_publication):
    limits = MemoryHost().limits()
    manifest, evidence = memory_evidence(limits)
    raw = storage.encoded(evidence)
    verification = {"manifest_sha256": "b" * 64, **resources.verify_memory(raw, manifest, limits)}
    store = storage.Store(tmp_path, clock=lambda: fake_publication[0][0])
    storage.put(store.path("host_memory_validation.json"), raw)
    monkeypatch.setattr(deployment, "verify_operation", lambda *a, **k: verification)
    result = recorder.activate(store, commit="a" * 40, manifest_hash="b" * 64)
    record = result["record"]
    assert record["host_memory_budget"] == verification["host_memory_budget"]
    assert store.bytes(record["input_hashes"]["host_memory_validation"]) == raw
    assert storage.load(store.path("activation.json"))["host_memory_budget"]["required_bytes"] == 1680 * MIB


def test_real_activation_verifier_requires_memory_evidence_before_any_activation(tmp_path, monkeypatch, operation_verification):
    root, data = tmp_path / "installed", tmp_path / "data"
    module = "deploy/collector-bb003/environment.py"
    storage.put(root / module, b'def inventory(root):\n    return {}\n')
    manifest = {"code_root": str(root), "data_root": str(data), "files": {module: storage.sha((root / module).read_bytes())},
                "environment_sha256": storage.sha(storage.encoded({}))}
    storage.put(root / deployment.MANIFEST, storage.encoded(manifest))
    monkeypatch.setattr(deployment, "CODE_ROOT", root)
    monkeypatch.setattr(deployment, "DATA_ROOT", data)
    monkeypatch.setattr(deployment, "__file__", str(root / "backtesting/experiments/bb003/deployment.py"))
    store = storage.Store(data)
    seal = {"scope": "full_morning_seal_including_verification_and_lock_release", "verified_worst_case": True,
            "host": os.uname().nodename, "source_files": manifest["files"], "environment_sha256": manifest["environment_sha256"],
            "worst_wall_seconds": 170, "peak_rss_mib": 500}
    storage.put(store.path("seal_runtime_validation.json"), storage.encoded(seal))
    with pytest.raises(deployment.NotReady, match="server_effective_memory"):
        operation_verification(store, mode="activate")
    limits = MemoryHost().limits()
    _, memory = memory_evidence(limits)
    memory.update(source_files=manifest["files"], environment_sha256=manifest["environment_sha256"])
    storage.put(store.path("host_memory_validation.json"), storage.encoded(memory))
    monkeypatch.setattr(resources, "host_limits", lambda: limits)
    assert operation_verification(store, mode="activate")["host_memory_budget"]["required_bytes"] == 1680 * MIB
    memory["heavy_job_peak_bytes"][resources.WORKERS[0]] = 499 * MIB
    store.path("host_memory_validation.json").write_bytes(storage.encoded(memory))
    with pytest.raises(ValueError, match="understates its verified morning peak"):
        operation_verification(store, mode="activate")
    assert not store.proof("activation", "activation")
