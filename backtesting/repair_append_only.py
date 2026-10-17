"""Explicit tail repair: python -m backtesting.repair_append_only --help."""
from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

from backtesting.append_only import TornTailError, atomic_replace, file_lock, fsync_directory, text_buffer
from backtesting.experiment_registry import _registry_rows
from backtesting.holdout import _ledger_rows


def repair(path: Path, kind: str, *, write: bool = False) -> None:
    validator = {"registry": _registry_rows, "holdout": _ledger_rows}[kind]
    with file_lock(path):
        original = path.read_bytes()
        try:
            validator(text_buffer(original, path))
        except TornTailError as exc:
            offset, line = exc.offset, exc.line
        else:
            print(f"{path}: no torn tail; unchanged")
            return
        prefix, tail = original[:offset], original[offset:]
        # A malformed interior record or header must never be repaired as a tail.
        if prefix:
            validator(text_buffer(prefix, path))
        print(f"{path}: torn tail from line {line}, byte offset {offset} ({len(tail)} bytes)")
        print(f"Torn bytes: {tail!r}")
        if not write:
            print("Dry-run: unchanged. Add --write to quarantine these bytes and truncate.")
            return
        descriptor, name = tempfile.mkstemp(prefix=f"{path.name}.quarantine.", dir=path.parent)
        quarantine = Path(name)
        # Make the quarantine durable before removing any bytes from the target.
        # On failure, retain it for inspection; retries use a new sidecar name.
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(tail)
            handle.flush()
            os.fsync(handle.fileno())
        fsync_directory(path.parent)
        atomic_replace(path, prefix)
        print(f"Quarantined to {quarantine}; truncated {path} to {offset} bytes")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("registry", "holdout"), required=True)
    parser.add_argument("path", type=Path)
    parser.add_argument("--write", action="store_true", help="quarantine the tail and replace the file")
    args = parser.parse_args(argv)
    try:
        repair(args.path.resolve(), args.kind, write=args.write)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Repair failed: {exc}\n")


if __name__ == "__main__":
    main()
