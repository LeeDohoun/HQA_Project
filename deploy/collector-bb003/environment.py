"""Hash a private interpreter and installed distribution files (no pip/network)."""
import hashlib
from importlib import metadata
from pathlib import Path
import sys


def digest(path):
    checksum = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def inventory(root):
    root = Path(root).resolve()
    if Path(sys.prefix).resolve() != root:
        raise ValueError("environment inventory must run with the private venv interpreter")
    result = {"interpreter": digest(Path(sys.executable).resolve()),
              "pyvenv_cfg": digest(root / "pyvenv.cfg"), "distributions": {}}
    for distribution in metadata.distributions():
        if not distribution.files or not any(str(p).endswith(".dist-info/RECORD") for p in distribution.files):
            raise ValueError("private distribution has no RECORD")
        files = {}
        for relative in distribution.files:
            path = Path(distribution.locate_file(relative))
            if not path.resolve().is_relative_to(root):
                raise ValueError("private distribution escapes the BB003 venv")
            if path.is_file():
                files[path.relative_to(root).as_posix()] = digest(path)
        result["distributions"][distribution.metadata["Name"]] = {"version": distribution.version, "files": files}
    return result


if __name__ == "__main__":
    import json
    print(json.dumps(inventory(sys.prefix), sort_keys=True, separators=(",", ":")))
