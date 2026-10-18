"""Research-side reader of completed, fixed-scope, non-H1 publications only."""
from datetime import date, datetime
from pathlib import Path

from backtesting.experiment_registry import PROJECT_ROOT

from . import EXPERIMENT_ID, Refused
from .storage import aware, digest, file_hash, safe_path, verified

SCOPE = "lh003-all-archives-non-2026H1-v1"
DAILY = ("market/krx_daily", "disclosures/dart_full/list", "disclosures/dart_full/list_supplement")
REFERENCES = ("reference/corp_codes.csv", "reference/dart_company/companies.jsonl")
COVERAGE = "lh003_coverage.json"
CODE_REFERENCE = "research/experiments/LH003_llm_leader_forward/distribution_code_reference.json"


def code_pins(state_root, repo_root, *, at, historical=False):
    """Use the start/amendment chain, or the fixed reviewed pre-start baseline."""
    from . import pins
    from .runner import implementation_hashes

    start = Path(state_root) / "start.json" if state_root is not None else None
    if start is not None and start.exists() and aware(at) >= aware(verified(start)["at"]):
        root, reference = state_root, None
    else:
        root, reference = None, verified(Path(repo_root) / CODE_REFERENCE)
    return pins.verify(root, repo_root, at=at, current={} if historical else implementation_hashes(repo_root),
                       historical=historical, reference=reference)


def protected(day):
    stamp = str(day)
    try:
        if len(stamp) == 8 and all("0" <= c <= "9" for c in stamp):
            parsed = datetime.strptime(stamp, "%Y%m%d").date()
        else:
            parsed = date.fromisoformat(stamp)
            if stamp != parsed.isoformat():
                raise ValueError("noncanonical date")
    except ValueError as exc:
        raise Refused("invalid archive date; refuse an unclassifiable row: " + stamp) from exc
    return date(2026, 1, 1) <= parsed <= date(2026, 6, 30)


class Distribution:
    def __init__(self, manifest_path, *, expected_hash, at, allow_synthetic=False,
                 state_root=None, repo_root=PROJECT_ROOT):
        self.state_root, self.repo_root = state_root, repo_root
        self.root = Path(manifest_path).resolve().parent
        self.manifest = verified(self.root / "manifest.json")
        value = self.manifest
        if (digest(value) != expected_hash or self.root.name != expected_hash
                or value["experiment_id"] != EXPERIMENT_ID or value["scope"] != SCOPE
                or aware(value["captured_at"]) > aware(at)):
            raise Refused("distribution manifest identity, scope or capture time differs")
        if value["synthetic"] and not allow_synthetic:
            raise Refused("synthetic distribution cannot produce observations")
        self.verify_code()
        for relative in value["files"]:
            self.path(relative)

    def verify_code(self):
        expected = code_pins(self.state_root, self.repo_root, at=self.manifest["captured_at"], historical=True)
        if self.manifest.get("code_pins") != expected:
            raise Refused("distribution generating code/amendment hashes differ from approved reference")

    def path(self, relative):
        path = safe_path(self.root, relative)
        if (relative not in self.manifest["files"] or path.is_symlink() or not path.is_file()
                or path.stat().st_mode & 0o222 or file_hash(path) != self.manifest["files"][relative]):
            raise Refused("distribution file missing, changed or writable: " + relative)
        return path


def latest(directory, *, sync_manifest, at, allow_synthetic=False, state_root=None, repo_root=PROJECT_ROOT):
    """A failed latest attempt cannot masquerade as a successful older update."""
    directory = Path(directory)
    starts = sorted((directory / "publications").glob("*.started.json"))
    if not starts:
        raise Refused("no completed LH003 distribution publication")
    if [int(p.name.split('.')[0]) for p in starts] != list(range(len(starts))):
        raise Refused("distribution publication journal is not contiguous")
    started = verified(starts[-1])
    completion = starts[-1].with_name(starts[-1].name.replace(".started", ".complete"))
    if not completion.is_file():
        raise Refused("latest distribution publication failed or is incomplete")
    receipt = verified(completion)
    if (receipt["started_sha256"] != digest(started)
            or started["sync_manifest_sha256"] != file_hash(sync_manifest)
            or aware(receipt["publication_finished_at"]) > aware(at)
            or aware(receipt["publication_finished_at"]) < aware(started["at"])):
        raise Refused("distribution does not match this sync or publication time")
    path = safe_path(directory, receipt["manifest_path"])
    publication = Distribution(path, expected_hash=receipt["manifest_sha256"], at=at,
                               allow_synthetic=allow_synthetic,
                               state_root=directory.parent if state_root is None else state_root, repo_root=repo_root)
    if publication.manifest["sync_manifest_sha256"] != started["sync_manifest_sha256"]:
        raise Refused("distribution completion differs from its sync")
    return publication, {**receipt, "completion_path": str(completion.resolve()),
                         "completion_sha256": digest(receipt), "manifest_path": str(path.resolve())}
