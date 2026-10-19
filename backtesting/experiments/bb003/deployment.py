"""BB003 source inventory, manifest verification and separate-root contracts."""
import ast
from contextlib import contextmanager
import fcntl
from functools import wraps
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time as timer
from datetime import time, timedelta

from . import KST, NotReady, aware, now
from .storage import MAIN, encoded, sha

CODE_ROOT = Path("/opt/hqa-bb003")
DATA_ROOT = Path("/var/lib/hqa/data")
MANIFEST = "deploy/collector-bb003/manifest.json"
PREREG = "research/experiments/BB003_buyback_forward/preregistration.md"
PREREG_COMMIT = "1e24337f962fba5e1d64cea9b22d10e301bff3ba"
PREREG_SHA256 = "c5cf76a3e06f7815b455338c8e7c47f60726ae49b42da03fdacb1d4114333e87"
HEAVY_LOCK = Path("/run/hqa-heavy/run.lock")
RUN_BUDGET_SECONDS, STOP_GRACE_SECONDS = 600, 5
FROZEN_DIRS = ("src/", "scripts/data/", "scripts/ops/", "scripts/forward/", "deploy/collector/",
               "research/experiments/FW001_forward_replication/")
FROZEN_FILES = {"scripts/__init__.py", *("backtesting/" + n + ".py" for n in
    ("__init__", "append_only", "capacity", "cost_model", "experiment_registry", "holdout", "signal_eval")),
    *("backtesting/experiments/" + n + ".py" for n in
    ("__init__", "common", "d001", "hc001", "hc002", "hc003", "hf001", "av001", "ordering"))}


def frozen_path(name):
    return name.startswith(FROZEN_DIRS) or name in FROZEN_FILES or Path(name).name.startswith("requirements") and Path(name).suffix == ".txt"


def rehearsal_output(path, source, repo):
    output = Path(path).resolve()
    roots = (MAIN.resolve(), Path(source).resolve(), Path(repo).resolve(), Path("/opt/hqa"),
             CODE_ROOT, DATA_ROOT, Path("/etc"))
    if any(output.is_relative_to(r) or r.is_relative_to(output) for r in roots):
        raise ValueError("rehearsal output overlaps an operational/source/code tree")
    if output.exists() or any(part.startswith(".env") for part in output.parts):
        raise ValueError("rehearsal requires a fresh throwaway output root")
    return output


def inventory(root, *, names=None, read=None):
    """FW002-style AST import closure, including local package initializers.

    Frozen imports are copied byte-for-byte into the *separate* deployment root;
    this function never generates or edits files in the checkout inventory.
    """
    root = Path(root)
    exists = (lambda p: p in names) if names is not None else lambda p: (root / p).is_file()
    read = read or (lambda p: (root / p).read_text(encoding="utf-8"))
    pending = ["scripts/bb003/__main__.py"]
    pending += ([p for p in names if p.startswith("backtesting/experiments/bb003/") and p.endswith(".py")]
                if names is not None else [p.relative_to(root).as_posix() for p in (root / "backtesting/experiments/bb003").glob("*.py")])
    found = set()

    def module(name, required=False):
        if name.split(".")[0] not in {"src", "backtesting", "scripts"}:
            return
        stem = name.replace(".", "/")
        match = next((p for p in (stem + ".py", stem + "/__init__.py") if exists(p)), None)
        if match:
            pending.append(match)
        elif required:
            namespace = any(p.startswith(stem + "/") for p in names) if names is not None else (root / stem).is_dir()
            if not namespace:
                raise ValueError("missing local BB003 import: " + name)

    while pending:
        relative = pending.pop()
        if relative in found:
            continue
        if not exists(relative):
            raise ValueError("missing BB003 source: " + relative)
        found.add(relative)
        path = Path(relative)
        for parent in path.parents:
            init = (parent / "__init__.py").as_posix()
            if exists(init):
                pending.append(init)
        for node in ast.walk(ast.parse(read(relative), filename=relative)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module(alias.name, True)
            elif isinstance(node, ast.ImportFrom):
                prefix = list(path.parent.parts)[:len(path.parent.parts) - node.level + 1] if node.level else []
                base = ".".join(prefix + ([node.module] if node.module else []))
                if node.module:
                    module(base, True)
                for alias in node.names:
                    module(base + "." + alias.name)
    artifacts = ([p for p in names if p.startswith("deploy/collector-bb003/")]
                 if names is not None else [p.relative_to(root).as_posix() for p in (root / "deploy/collector-bb003").rglob("*") if p.is_file()])
    result = found | set(artifacts) | {PREREG, "research/experiments/BB001_direct_buyback/preregistration.md",
        "research/experiments/BB003_buyback_forward/implementation_notes.md"}
    result.discard(MANIFEST)
    return sorted(p for p in result if "__pycache__" not in p and not p.endswith(".pyc"))


def verify(root, *, raw=None):
    root = Path(root).resolve()
    manifest = json.loads(raw if raw is not None else (root / MANIFEST).read_bytes())
    if manifest["code_root"] != str(CODE_ROOT) or manifest["data_root"] != str(DATA_ROOT):
        raise ValueError("BB003 manifest root binding differs")
    for name, expected in manifest["files"].items():
        path = root / name
        if (Path(name).is_absolute() or not path.resolve().is_relative_to(root)
                or path.is_symlink() or any(p.startswith(".env") for p in path.parts)
                or sha(path.read_bytes()) != expected):
            raise ValueError("BB003 deployment inventory differs: " + name)
    if (root / "venv").is_symlink() or (root / "venv/pyvenv.cfg").is_symlink():
        raise ValueError("BB003 needs a private venv")
    from importlib.util import spec_from_file_location, module_from_spec
    spec = spec_from_file_location("bb003_environment", root / "deploy/collector-bb003/environment.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    if sha(encoded(module.inventory(root / "venv"))) != manifest["environment_sha256"]:
        raise ValueError("BB003 interpreter/installed environment differs")
    return manifest


def verify_seal_runtime(raw, manifest):
    """Operator's final-host full-path measurement, bound to private bytes.

    A historical replay (including measure.py) cannot attest to private-env
    verification, real admission/lock contention or the worst server workload.
    """
    from .admission import SEAL_BUDGET_SECONDS
    evidence = json.loads(raw)
    if (evidence.get("scope") != "full_morning_seal_including_verification_and_lock_release"
            or evidence.get("verified_worst_case") is not True
            or evidence.get("host") != os.uname().nodename
            or evidence.get("source_files") != manifest["files"]
            or evidence.get("environment_sha256") != manifest["environment_sha256"]):
        raise ValueError("BB003 needs final-server/private-environment seal runtime evidence on these exact sources")
    for field, limit in (("worst_wall_seconds", SEAL_BUDGET_SECONDS), ("peak_rss_mib", 600)):
        value = evidence.get(field)
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value < limit:
            raise ValueError("BB003 verified morning seal exceeds its runtime/RSS cap: " + field)
    return sha(raw)


def verify_operation(store, *, mode="record"):
    """The sole operational code/environment/activation/amendment verifier.

    PC operations use the transported sealed manifest, with the same source and
    private-environment checks as the installed worker. Rehearsals are labelled
    separately and can never supply a forward verification.
    """
    root = Path(__file__).resolve().parents[3]
    if store.measurement:
        return {"manifest_sha256": sha(encoded({p: sha((root / p).read_bytes()) for p in inventory(root)})),
                "measurement": True}
    activation = store.get("activation", "activation")
    if mode == "evaluation" and root == CODE_ROOT:
        raise ValueError("BB003 final evaluation/publication requires the PC checkout")
    if mode not in {"pc", "evaluation", "setup", "activate", "bugfix"} and not activation:
        raise NotReady("BB003 is not activated by its fixed cutoff")
    if mode not in {"pc", "evaluation"} and root != CODE_ROOT:
        raise ValueError("production BB003 requires its own /opt/hqa-bb003 root")
    if mode in {"pc", "evaluation"} and root != CODE_ROOT and activation:
        checksum = activation.get("input_hashes", {}).get("manifest")
        if store.keys("bugfixes"):
            checksum = store.get("bugfixes", store.keys("bugfixes")[-1])["input_hashes"]["after_manifest"]
        if not checksum:
            raise NotReady("activation_manifest_bytes_missing")
        raw = store.bytes(checksum)
    else:
        raw = (root / MANIFEST).read_bytes()
    manifest = verify(root, raw=raw)
    digest = sha(raw)
    seal_evidence = {}
    if mode == "activate" or activation and root == CODE_ROOT and mode == "record":
        path = store.path("seal_runtime_validation.json")
        if not path.is_file():
            raise NotReady("server_morning_seal_runtime_unverified")
        seal_evidence["seal_runtime_validation_sha256"] = verify_seal_runtime(path.read_bytes(), manifest)
        from .resources import host_limits, verify_memory
        path = store.path("host_memory_validation.json")
        if not path.is_file():
            raise NotReady("server_effective_memory_and_job_peaks_unverified")
        memory_raw = path.read_bytes()
        seal_evidence.update(verify_memory(memory_raw, manifest, host_limits()))
        memory = json.loads(memory_raw)
        seal_peak = json.loads(store.path("seal_runtime_validation.json").read_bytes())["peak_rss_mib"] * 2**20
        if memory["heavy_job_peak_bytes"]["hqa-bb003-daily.service"] < seal_peak:
            raise ValueError("BB003 host memory evidence understates its verified morning peak")
    if activation:
        from .amendments import binding
        binding(store, digest)
        if mode not in {"setup", "activate", "bugfix"}:
            from .policy import latest
            latest(store)
    return {"manifest_sha256": digest, "source_files": manifest["files"], **seal_evidence}


@contextmanager
def operation(store, *, mode="record", name):
    """Keep verification and terminal error handling around the entire entry."""
    previous = getattr(store, "_verification_mode", "record")
    store._verification_mode = mode
    try:
        verify_operation(store, mode=mode)
        yield
    except BaseException as exc:
        if mode == "evaluation":
            started = store.proof("evaluation_started", "final")
            if started and (name in {"publish_trial", "publish-trial"} or not store.proof("evaluation", "final")):
                from .evaluate import abort_evaluation
                abort_evaluation(store, started, exc, phase=name)
        raise
    finally:
        store._verification_mode = previous


def verified(mode="record"):
    """Verify before inputs; Store verifies each seal again."""
    def decorate(function):
        @wraps(function)
        def call(store, *args, **kwargs):
            with operation(store, mode=mode, name=function.__name__):
                return function(store, *args, **kwargs)
        return call
    return decorate


def deadline_priority(*, source=DATA_ROOT, state=None, clock=now, run=subprocess.check_output, verify=True):
    """Bulk preparation stays outside morning and upcoming priority triggers."""
    from .admission import FW002_TIMERS, fw002_ready, installed_windows, refuse_active
    stamp = aware(clock())
    if time(5, 30) < stamp.time() < time(9, 1):
        raise NotReady("night_preparation_closed_use_morning_seal")
    windows, installed = installed_windows(stamp, run=run)
    refuse_active(run)
    stamp = aware(clock())
    finish = stamp + timedelta(seconds=RUN_BUDGET_SECONDS + STOP_GRACE_SECONDS)
    fw002_ready(source, state, stamp, installed, until=finish, verify=verify)
    for earliest, latest, unit in windows:
        if unit not in FW002_TIMERS and latest >= stamp and earliest <= finish:
            raise NotReady("higher_priority_trigger_within_preparation_budget:" + unit + ":" + earliest.isoformat())
    refuse_active(run)


@contextmanager
def operational_lock(*, phase="prepare", source=DATA_ROOT, state=None, clock=now, run=None):
    # These cheap checks run before code/environment hashing, on both sides of
    # flock. The kernel alarm also bounds manual CLI execution, including C I/O.
    if signal.getitimer(signal.ITIMER_REAL) != (0., 0.):
        raise ValueError("BB003 cannot replace an existing process deadline")
    previous = signal.signal(signal.SIGALRM, signal.SIG_DFL)
    from .admission import SEAL_BUDGET_SECONDS, missed, morning
    budget = SEAL_BUDGET_SECONDS if phase == "seal" else RUN_BUDGET_SECONDS
    begun = timer.monotonic()
    signal.setitimer(signal.ITIMER_REAL, budget)
    try:
        guard = (lambda: morning(source, state, clock=clock, run=run,
            budget=max(0, budget - (timer.monotonic() - begun)))) if phase == "seal" else (
                lambda: deadline_priority(source=source, state=state, clock=clock,
                                         run=subprocess.check_output if run is None else run))
        guard()
        with heavy_lock():
            guard()
            final_guard = (lambda: morning(source, state, clock=clock, run=run, verify=True,
                budget=max(0, budget - (timer.monotonic() - begun)))) if phase == "seal" else (
                    lambda: deadline_priority(source=source, state=state, clock=clock,
                        run=subprocess.check_output if run is None else run, verify=True))
            yield final_guard
    except ValueError as exc:
        if phase == "seal":
            missed(state, str(exc), clock=clock)
        raise
    except BlockingIOError:
        if phase == "seal":
            missed(state, "shared_heavy_lock_busy", clock=clock)
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def fw001_contract(run=subprocess.check_output):
    result = {}
    for unit in ("hqa-fw001-daily.service", "hqa-shadow-daily.service"):
        text = run(["systemctl", "show", unit, "--property=LoadState,NeedDaemonReload,ExecStart,RuntimeDirectoryPreserve,Type,RemainAfterExit"], text=True)
        fields = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        if (fields.get("LoadState") != "loaded" or fields.get("NeedDaemonReload") != "no"
                or "/usr/bin/flock" not in fields.get("ExecStart", "")
                or "--exclusive" not in fields.get("ExecStart", "")
                or "--no-fork" not in fields.get("ExecStart", "")
                or str(HEAVY_LOCK) not in fields.get("ExecStart", "")
                or fields.get("Type") != "oneshot" or fields.get("RemainAfterExit") != "no"
                or fields.get("RuntimeDirectoryPreserve") != "yes"):
            raise ValueError("BB003 requires FW001 d22e231 shared heavy-lock contract: " + unit)
        result[unit] = fields
    return result


@contextmanager
def heavy_lock(path=HEAVY_LOCK):
    # The FW001 owner creates/preserves this inode. Opening read-only allows the
    # BB003 sandbox's *only* writable path to remain forward/bb003.
    with Path(path).open("rb") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if os.fstat(handle.fileno()).st_ino != Path(path).stat().st_ino:
            raise ValueError("shared heavy-lock inode changed")
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
