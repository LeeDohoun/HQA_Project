"""Sample resident memory of this process and all living descendants on Linux."""
from __future__ import annotations

import os
import threading
from pathlib import Path


class ProcessTreeMemory:
    interval_seconds = 0.01

    def __init__(self):
        self.peak_mib = self.parent_peak_mib = self.children_peak_mib = 0.0
        self.samples = 0
        self.error = None
        self.done = threading.Event()

    def sample(self):
        try:
            entries = {}
            for path in Path("/proc").iterdir():
                if not path.name.isdigit():
                    continue
                try:
                    fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
                    entries[int(path.name)] = int(fields[1]), int(fields[21]) * os.sysconf("SC_PAGE_SIZE") / 2**20
                except (FileNotFoundError, ProcessLookupError):
                    continue
            descendants, pending = {os.getpid()}, {os.getpid()}
            while pending:
                pending = {pid for pid, (parent, _) in entries.items() if parent in pending and pid not in descendants}
                descendants.update(pending)
            parent = entries[os.getpid()][1]
            children = sum(entries[pid][1] for pid in descendants if pid != os.getpid())
            self.peak_mib = max(self.peak_mib, parent + children)
            self.parent_peak_mib = max(self.parent_peak_mib, parent)
            self.children_peak_mib = max(self.children_peak_mib, children)
            self.samples += 1
        except (OSError, KeyError, ValueError) as exc:
            self.error = str(exc)

    def _run(self):
        while not self.done.wait(self.interval_seconds):
            self.sample()

    def __enter__(self):
        self.sample()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.done.set()
        self.thread.join()
        self.sample()

    def report(self):
        return {"peak_rss_mib": self.peak_mib, "parent_peak_rss_mib": self.parent_peak_mib,
                "children_peak_rss_mib": self.children_peak_mib, "includes_children": True,
                "sample_interval_seconds": self.interval_seconds, "samples": self.samples,
                "verified": self.samples > 0 and self.error is None, "error": self.error}
