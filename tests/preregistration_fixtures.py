"""Committed preregistrations in disposable, offline Git repositories."""
import subprocess
from pathlib import Path

from backtesting import experiment_registry


def committed_preregistrations(root, experiment_ids):
    def git(*args):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)

    # Preserve pinned preregistration commits (notably EV001) using local objects.
    subprocess.run(["git", "clone", "--shared", "--no-checkout", "--quiet", "--local",
                    str(experiment_registry.PROJECT_ROOT), str(root)], check=True, capture_output=True)
    for experiment_id in experiment_ids:
        relative = Path("research/experiments") / experiment_id / "preregistration.md"
        git("checkout", "HEAD", "--", relative.as_posix())
    return {name: experiment_registry.verify_preregistration(name, root) for name in experiment_ids}
