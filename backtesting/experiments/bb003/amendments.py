"""Auditable BB003 bugfix manifests; never modifies any implementation file."""
import json
import re

from . import aware
from .deployment import frozen_path, verified
from .storage import sha


def binding(store, current_hash):
    activation = store.get("activation", "activation")
    expected = activation["manifest_sha256"]
    keys = store.keys("bugfixes")
    if keys != [f"{i:06d}" for i in range(len(keys))]:
        raise ValueError("BB003 bugfix journal is not contiguous")
    for key in keys:
        record = store.get("bugfixes", key)
        if record["before_sha256"] != expected:
            raise ValueError("BB003 bugfix manifest chain differs")
        for checksum in record["input_hashes"].values():
            store.bytes(checksum)
        expected = record["after_sha256"]
    if current_hash != expected:
        raise ValueError("BB003 active manifest changed without a reviewed bugfix journal")
    return expected


@verified("bugfix")
def record_bugfix(store, before_raw, after_raw, *, reason, commit):
    before, after = json.loads(before_raw), json.loads(after_raw)
    binding(store, sha(before_raw))
    if not reason.strip() or not re.fullmatch("[0-9a-f]{40}", commit) or before == after:
        raise ValueError("bugfix needs a concrete reason, full commit and changed manifest")
    changed = {name for name in set(before["files"]) | set(after["files"])
               if before["files"].get(name) != after["files"].get(name)}
    allowed = ("backtesting/experiments/bb003/", "scripts/bb003/", "deploy/collector-bb003/")
    if not changed or any(frozen_path(name) or not name.startswith(allowed) for name in changed):
        raise ValueError("bugfix may change only BB003 implementation, never FW001/preregistrations")
    if any(after.get(name) != before.get(name) for name in ("experiment_id", "code_root", "data_root")):
        raise ValueError("bugfix cannot change experiment/root identity")
    return store.seal("bugfixes", f"{len(store.keys('bugfixes')):06d}", {
        "kind": "operator_reviewed_bugfix_only", "reason": reason, "commit": commit,
        "time": aware(store.clock()).isoformat(), "before_sha256": sha(before_raw), "after_sha256": sha(after_raw),
        "changed_files": sorted(changed), "input_hashes": {"before_manifest": store.blob(before_raw),
                                                          "after_manifest": store.blob(after_raw)}})
