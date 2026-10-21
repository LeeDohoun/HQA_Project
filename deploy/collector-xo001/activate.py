"""Coordinator convenience entry: executes the normal, evidence-checked activation CLI."""
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.xo001.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main(["activate", *sys.argv[1:]]))
