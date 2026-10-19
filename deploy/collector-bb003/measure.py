"""Fresh-process offline replay, including startup wall time and peak RSS."""
import argparse
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backtesting.experiments.bb003.storage import encoded, load, put, sha


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-data", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--from-date", default="2025-07-01")
    parser.add_argument("--to-date", default="2025-12-29")
    parser.add_argument("--synthetic", action="store_true")
    args = parser.parse_args(argv)
    command = [sys.executable, "-B", "-m", "scripts.bb003", "rehearse", "--execute",
        "--source-data", str(args.source_data), "--output-root", str(args.output_root),
        "--from-date", args.from_date, "--to-date", args.to_date]
    if args.synthetic:
        command.append("--synthetic")
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHON_DOTENV_DISABLED": "1",
           "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
    begun = time.perf_counter()
    subprocess.run(command, cwd=ROOT, env=env, check=True, timeout=3600, stdout=subprocess.DEVNULL)
    raw = (args.output_root / "measurement.json").read_bytes()
    report = json.loads(raw)
    result = {"command": command, "source_files": report["source_files"],
        "measurement_sha256": sha(raw), "process_wall_seconds": time.perf_counter() - begun,
        "process_peak_rss_mib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024,
        "synthetic": args.synthetic, "full_seal_and_post_entry": report["counts"].get("included", 0) > 0
            and any(s["post_entry"].get("executed", 0) for s in report["samples"]),
        "two_phase": report["two_phase"], "seal_max_seconds": report["seal_max_seconds"],
        "host": os.uname().nodename, "successful": True}
    put(args.output_root / "process_measurement.json", encoded(result))
    print(json.dumps({k: v for k, v in result.items() if k != "source_files"}, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
