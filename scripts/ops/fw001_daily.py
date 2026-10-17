"""Record FW001 after verified local KRX completion; dry-run by default."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import resource
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.forward import fw001


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("HQA_DATA_DIR", "data")))
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    started = time.perf_counter()
    report = fw001.daily(args.data_dir, execute=args.execute)
    report.update(runtime_seconds=time.perf_counter() - started,
                  peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    return 0 if report["status"] in ("ok", "dry_run") else 1


if __name__ == "__main__":
    raise SystemExit(main())
