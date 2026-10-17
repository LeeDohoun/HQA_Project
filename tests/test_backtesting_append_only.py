"""Fault injection uses temporary files only; no research runs or live ledgers."""
from __future__ import annotations

import errno
import io
import json
import multiprocessing
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backtesting import append_only, experiment_registry, holdout
from backtesting.repair_append_only import repair


@pytest.fixture(params=["registry", "holdout"])
def target(tmp_path, monkeypatch, request):
    kind = request.param
    path = tmp_path / ("registry.csv" if kind == "registry" else "holdout_ledger.jsonl")
    registration = SimpleNamespace(commit_hash="a" * 40, fields={"uses_holdout": True})
    monkeypatch.setattr(experiment_registry, "verify_preregistration", lambda *a: registration)
    monkeypatch.setattr(holdout, "verify_preregistration", lambda *a: registration)

    def append(name):
        if kind == "registry":
            experiment_registry.record_trial(name, name, {"설명": "a,b"}, "fail", note="one\ntwo",
                                             repo_root=tmp_path, registry_path=path)
        else:
            holdout.guard_period("20260101", "20260630", experiment_id=name,
                                 repo_root=tmp_path, ledger_path=path)

    def rows():
        with append_only.text_buffer(path.read_bytes(), path) as handle:
            return (experiment_registry._registry_rows(handle) if kind == "registry"
                    else holdout._ledger_rows(handle))

    append("seed")
    # Exercise exact preservation of noncanonical whitespace, CRLF, and Unicode.
    if kind == "holdout":
        row = rows()[0]
        row["extra"] = "한글\u2028separator"
        path.write_bytes((json.dumps(row, ensure_ascii=False, indent=None, separators=(", ", ": "))
                          + "\r\n").encode("utf-8"))
    return kind, path, append, rows


def test_prefix_preserved_exactly(target):
    _, path, append, rows = target
    path.chmod(0o640)
    original = path.read_bytes()
    append("next")
    assert path.read_bytes()[:len(original)] == original
    assert len(rows()) == 2
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


@pytest.mark.parametrize("failure", ["write", "fsync", "replace"])
def test_io_error_before_replace_leaves_original_intact(target, monkeypatch, failure):
    _, path, append, rows = target
    original = path.read_bytes()
    with monkeypatch.context() as patch:
        if failure == "write":
            fdopen = os.fdopen

            class PartialWrite(io.BufferedWriter):
                def write(self, content):
                    super().write(content[:len(content) // 2])
                    raise OSError(errno.ENOSPC, "injected disk full")

            patch.setattr(append_only.os, "fdopen", lambda fd, mode: PartialWrite(fdopen(fd, mode)))
        else:
            def fail(*args):
                raise OSError(errno.EIO, f"injected {failure} failure")

            patch.setattr(append_only.os, "fsync" if failure == "fsync" else "replace", fail)
        with pytest.raises(OSError, match="injected"):
            append("failed")
    assert path.read_bytes() == original
    assert len(rows()) == 1
    assert not list(path.parent.glob(f".{path.name}.*.tmp"))
    append("retry")
    assert len(rows()) == 2


@pytest.mark.parametrize("existing", [False, True])
def test_process_death_before_replace_leaves_original_intact(target, existing):
    _, path, append, rows = target
    if not existing:
        path.unlink()
    original = path.read_bytes() if existing else None

    def crash():
        append_only.os.replace = lambda *args: os._exit(75)
        append("killed")

    process = multiprocessing.get_context("fork").Process(target=crash)
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 75
    assert (path.read_bytes() if path.exists() else None) == original
    append("after-crash")
    assert len(rows()) == (2 if existing else 1)


def test_temp_fsync_replace_directory_fsync_order(target, monkeypatch):
    _, path, append, _ = target
    events = []
    fsync, replace = os.fsync, os.replace

    def sync(fd):
        events.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "temp")
        fsync(fd)

    def swap(source, destination):
        assert Path(source).parent == path.parent
        assert destination == path
        events.append("replace")
        replace(source, destination)

    monkeypatch.setattr(append_only.os, "fsync", sync)
    monkeypatch.setattr(append_only.os, "replace", swap)
    append("ordered")
    assert events == ["temp", "replace", "directory"]


def test_concurrent_process_appends_preserve_all_rows(target):
    _, path, append, rows = target
    original = path.read_bytes()
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(8)

    def worker(number):
        barrier.wait(timeout=10)
        append(f"worker{number}")

    processes = [context.Process(target=worker, args=(number,)) for number in range(8)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0
    assert path.read_bytes().startswith(original)
    assert {row["experiment_id"] for row in rows()} == {"seed", *(f"worker{i}" for i in range(8))}


@pytest.mark.parametrize("tail_type", ["partial", "multiline", "utf8", "terminated", "complete", "blank"])
def test_torn_tail_detected_and_explicitly_repaired(target, tail_type, capsys):
    kind, path, append, rows = target
    original = path.read_bytes()
    if kind == "registry":
        tails = {"partial": b"2026,broken", "multiline": b'2026,broken,a,v,fail,{},"one\ntwo\npartial',
                 "utf8": b"2026,broken,a,v,fail,{},\xe2\x82", "terminated": b"2026,broken\n",
                 "complete": b"2026,broken,a,v,fail,{},note", "blank": b"\r"}
    else:
        tails = {"partial": b'{"experiment_id":', "multiline": b'{"experiment_id": "unfinished',
                 "utf8": b'{"experiment_id":"\xe2\x82', "terminated": b'{"experiment_id":\n',
                 "complete": original.rstrip(b"\r\n"), "blank": b"\r"}
    tail = tails[tail_type]
    path.write_bytes(original + tail)
    line = original.count(b"\n") + 1
    with pytest.raises(append_only.TornTailError, match=rf"line {line}.*repair_append_only"):
        append("blocked")
    if kind == "registry":
        with pytest.raises(append_only.TornTailError, match=rf"line {line}"):
            experiment_registry.trial_count("seed", repo_root=path.parent, registry_path=path)
    repair(path, kind)
    output = capsys.readouterr().out
    assert "Dry-run" in output and repr(tail) in output
    assert path.read_bytes() == original + tail
    assert not list(path.parent.glob(f"{path.name}.quarantine.*"))
    repair(path, kind, write=True)
    assert path.read_bytes() == original
    assert [p.read_bytes() for p in path.parent.glob(f"{path.name}.quarantine.*")] == [tail]
    assert len(rows()) == 1
    append("after-repair")
    assert len(rows()) == 2


def test_repair_refuses_interior_corruption(target):
    kind, path, _, _ = target
    original = path.read_bytes()
    bad = b"bad,row\n" if kind == "registry" else b"{broken\n"
    path.write_bytes(original + bad + original)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        repair(path, kind, write=True)
    assert path.read_bytes() == before
    assert not list(path.parent.glob(f"{path.name}.quarantine.*"))


def test_repair_command_defaults_to_dry_run(target):
    kind, path, _, _ = target
    original, tail = path.read_bytes(), b"partial\xe2\x82"
    path.write_bytes(original + tail)
    command = [sys.executable, "-m", "backtesting.repair_append_only", "--kind", kind, str(path)]
    dry_run = subprocess.run(command, capture_output=True, text=True)
    assert dry_run.returncode == 0, dry_run.stderr
    assert "Dry-run" in dry_run.stdout and repr(tail) in dry_run.stdout
    assert path.read_bytes() == original + tail
    written = subprocess.run([*command, "--write"], capture_output=True, text=True)
    assert written.returncode == 0, written.stderr
    assert path.read_bytes() == original
    assert [p.read_bytes() for p in path.parent.glob(f"{path.name}.quarantine.*")] == [tail]


def test_failed_repair_keeps_original_and_quarantine(target, monkeypatch):
    kind, path, _, _ = target
    original, tail = path.read_bytes(), b"partial"
    path.write_bytes(original + tail)

    def fail(*args):
        raise OSError(errno.EIO, "injected replacement failure")

    monkeypatch.setattr(append_only.os, "replace", fail)
    with pytest.raises(OSError, match="replacement failure"):
        repair(path, kind, write=True)
    assert path.read_bytes() == original + tail
    assert [p.read_bytes() for p in path.parent.glob(f"{path.name}.quarantine.*")] == [tail]


def test_directory_sync_failure_reports_error_but_leaves_complete_content(target, monkeypatch):
    _, path, append, rows = target
    original, fsync = path.read_bytes(), os.fsync

    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EIO, "injected directory sync failure")
        fsync(fd)

    monkeypatch.setattr(append_only.os, "fsync", fail_directory)
    with pytest.raises(OSError, match="directory sync failure"):
        append("committed-before-sync-error")
    assert path.read_bytes().startswith(original)
    assert len(rows()) == 2


@pytest.mark.parametrize("tail", [b"recorded_", b'"recorded_at\npartial'])
def test_torn_registry_header_is_repairable(tmp_path, tail):
    path = tmp_path / "registry.csv"
    path.write_bytes(tail)
    with pytest.raises(append_only.TornTailError, match="line 1"):
        experiment_registry.trial_count("D001", registry_path=path)
    repair(path, "registry", write=True)
    assert path.read_bytes() == b""
    assert experiment_registry.trial_count("D001", registry_path=path) == 0
    assert [p.read_bytes() for p in tmp_path.glob("registry.csv.quarantine.*")] == [tail]


def test_registry_tail_after_blank_line_reports_exact_line_and_offset(tmp_path):
    path = tmp_path / "registry.csv"
    original = (",".join(experiment_registry.REGISTRY_COLUMNS) + "\r\n\r\n").encode()
    tail = b'2026,D001,a,v,fail,{},"one\ntorn'
    path.write_bytes(original + tail)
    with pytest.raises(append_only.TornTailError, match="line 3") as error:
        experiment_registry.trial_count("D001", registry_path=path)
    assert error.value.offset == len(original)
    repair(path, "registry", write=True)
    assert path.read_bytes() == original
