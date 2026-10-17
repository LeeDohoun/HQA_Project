"""Explicit completed listing checkpoints for offline experiment fixtures."""
from src.ingestion.dart_listing import digest, directory, reference_version, supplement_manifest, freeze_target_scan
from src.ingestion.dart_backfill import _write_json
from src.ingestion.storage import read_rows, write_rows


def checkpoint(data_dir, *, end="20261009", days=None, corps=()):
    root = directory(data_dir)
    corps = sorted(corps)
    for name, text in (("corp_codes.csv", "corp_code,stock_code\n"), ("dart_company/companies.jsonl", "")):
        path = root.parents[1] / "reference" / name
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    krx = root.parents[1] / "market/krx_daily"
    if not list(krx.glob("*/*.jsonl")):
        write_rows(krx / "2016/20160601.jsonl", [])
    counts = {corp: sum(row.get("corp_code") == corp for path in (root / "list_supplement").glob("*/*.jsonl")
                        for row in read_rows(path)) for corp in corps}
    state = {"start_de": "20160601", "end_de": end, "target_corps": corps,
             "target_set_hash": digest(corps), "completed_corps": corps,
             "reference_version": reference_version(data_dir),
             "rows_per_corp": counts, "skipped_per_corp": dict.fromkeys(corps, 0),
             "duplicates_per_corp": dict.fromkeys(corps, 0), "file_manifest": supplement_manifest(data_dir),
             "target_scan": freeze_target_scan(data_dir, end)}
    _write_json(root / "list_supplement/_state.json", state)
    if days is None and not (root / "_state.json").exists():
        days = [p.stem for p in (root / "list").glob("*/*.jsonl")]
    if days is not None:
        _write_json(root / "_state.json", {"completed_listing_days": sorted(days)})
    return state
