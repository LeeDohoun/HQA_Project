#!/usr/bin/env python3
"""JSONL 품질 진단 스크립트.

사용 예시:
    # 파일 단위 진단
    python scripts/inspect_raw_quality.py \
      data/raw/news/2차전지.jsonl data/raw/dart/2차전지.jsonl

    # 디렉토리 단위 진단(재귀적으로 *.jsonl 탐색)
    python scripts/inspect_raw_quality.py data/raw data/corpora/2차전지

    # combined corpus 파일 단위 진단
    python scripts/inspect_raw_quality.py data/corpora/2차전지/combined.jsonl

    # JSON 리포트 저장
    python scripts/inspect_raw_quality.py data/raw --json-out data/reports/raw_quality.json
"""

from __future__ import annotations

# File role:
# - Inspect raw/corpus JSONL files and summarize common data-quality problems.

import argparse
import json
import statistics
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


DART_WRAPPER_TOKENS = [
    "잠시만 기다려주세요",
    "현재목차",
    "본문선택",
    "첨부선택",
    "문서목차",
]

SUPPORTED_SOURCES = {"news", "forum", "dart", "combined"}


@dataclass
class ContentStats:
    count: int = 0
    min: int = 0
    max: int = 0
    mean: float = 0.0
    median: float = 0.0
    p95: float = 0.0


@dataclass
class SourceDiagnostics:
    source: str
    total_docs: int = 0
    has_title: int = 0
    has_content: int = 0
    has_page_content: int = 0
    normalized_has_source_type: int = 0
    normalized_has_title: int = 0
    normalized_has_content: int = 0
    normalized_has_page_content: int = 0
    title_eq_content: int = 0
    placeholder_suspects: int = 0
    low_quality_suspects: int = 0
    dart_wrapper_suspects: int = 0
    dart_has_body_but_wrapper: int = 0
    source_type_counts: Counter[str] = field(default_factory=Counter)
    normalized_source_type_counts: Counter[str] = field(default_factory=Counter)
    content_lengths: list[int] = field(default_factory=list)
    placeholder_samples: list[dict[str, Any]] = field(default_factory=list)
    title_only_samples: list[dict[str, Any]] = field(default_factory=list)
    wrapper_samples: list[dict[str, Any]] = field(default_factory=list)
    unknown_samples: list[dict[str, Any]] = field(default_factory=list)

    def add_sample(self, bucket: list[dict[str, Any]], record: dict[str, Any], path: Path, limit: int = 5) -> None:
        if len(bucket) >= limit:
            return
        bucket.append(
            {
                "path": str(path),
                "id": record.get("id"),
                "source_type": record.get("source_type"),
                "title": truncate_text(str(record.get("title", ""))),
                "content": truncate_text(extract_content(record)),
            }
        )


def truncate_text(text: str, limit: int = 100) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def get_str_from_paths(record: dict[str, Any], paths: list[tuple[str, ...]]) -> str:
    for path in paths:
        cur: Any = record
        found = True
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                found = False
                break
            cur = cur[key]
        if found and isinstance(cur, str) and cur.strip():
            return cur.strip()
    return ""


def get_any_from_paths(record: dict[str, Any], paths: list[tuple[str, ...]]) -> Any:
    for path in paths:
        cur: Any = record
        found = True
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                found = False
                break
            cur = cur[key]
        if found and cur is not None:
            return cur
    return None


@dataclass
class NormalizedRecord:
    source_type: str
    title: str
    content: str
    page_content: str
    has_body: bool


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="JSONL raw/corpora 품질 진단 도구")
    parser.add_argument(
        "inputs",
        nargs="+",
        help="JSONL 파일 경로 또는 디렉토리 경로 (디렉토리인 경우 재귀 탐색)",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="진단 결과를 JSON 파일로 저장할 경로",
    )
    return parser


def discover_jsonl_paths(inputs: list[str]) -> list[Path]:
    discovered: list[Path] = []
    for raw in inputs:
        path = Path(raw)
        if not path.exists():
            print(f"[WARN] 경로가 존재하지 않습니다: {path}")
            continue
        if path.is_file() and path.suffix.lower() == ".jsonl":
            discovered.append(path)
            continue
        if path.is_dir():
            discovered.extend(sorted(p for p in path.rglob("*.jsonl") if p.is_file()))
            continue
        print(f"[WARN] JSONL 파일/디렉토리가 아닙니다: {path}")
    unique = sorted({p.resolve() for p in discovered})
    return unique


def infer_source(path: Path) -> str | None:
    token_chain = [part.lower() for part in path.parts]
    name = path.name.lower()

    for token in token_chain + [name]:
        for source in SUPPORTED_SOURCES:
            if source in token:
                return source
    return None


def parse_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
        for line_no, line in enumerate(handle, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                print(f"[WARN] JSON 파싱 실패: {path}:{line_no}")
                continue
            if not isinstance(data, dict):
                print(f"[WARN] 객체(JSON object) 레코드가 아닙니다: {path}:{line_no}")
                continue
            rows.append(data)
    return rows


def extract_content(record: dict[str, Any]) -> str:
    content = record.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    page_content = record.get("page_content")
    if isinstance(page_content, str):
        return page_content.strip()
    return ""


def normalize_record(record: dict[str, Any]) -> NormalizedRecord:
    source_type = get_str_from_paths(
        record,
        [
            ("source_type",),
            ("metadata", "source_type"),
            ("metadata", "metadata", "source_type"),
        ],
    )
    title = get_str_from_paths(
        record,
        [
            ("title",),
            ("metadata", "title"),
            ("metadata", "metadata", "title"),
        ],
    )
    content = get_str_from_paths(
        record,
        [
            ("content",),
            ("metadata", "content"),
            ("page_content",),
            ("metadata", "page_content"),
        ],
    )
    page_content = get_str_from_paths(
        record,
        [
            ("page_content",),
            ("metadata", "page_content"),
            ("metadata", "metadata", "page_content"),
        ],
    )
    has_body_raw = get_any_from_paths(
        record,
        [
            ("has_body",),
            ("metadata", "has_body"),
            ("metadata", "metadata", "has_body"),
        ],
    )
    has_body = bool(has_body_raw) if has_body_raw is not None else False
    normalized_source_type = source_type.lower() if source_type else "unknown"
    return NormalizedRecord(
        source_type=normalized_source_type,
        title=title,
        content=content,
        page_content=page_content,
        has_body=has_body,
    )


def calc_stats(lengths: list[int]) -> ContentStats:
    if not lengths:
        return ContentStats(count=0)
    ordered = sorted(lengths)
    p95_index = max(0, int(round((len(ordered) - 1) * 0.95)))
    return ContentStats(
        count=len(ordered),
        min=ordered[0],
        max=ordered[-1],
        mean=round(statistics.fmean(ordered), 2),
        median=round(statistics.median(ordered), 2),
        p95=ordered[p95_index],
    )


def check_low_quality(source: str, content_len: int, source_type: str | None = None) -> bool:
    if source == "news":
        return content_len < 30
    if source == "forum":
        return content_len < 20
    if source == "dart":
        return content_len < 200
    if source == "combined":
        st = (source_type or "").lower()
        if st == "news":
            return content_len < 30
        if st == "forum":
            return content_len < 20
        if st == "dart":
            return content_len < 200
        return content_len < 30
    return False


def analyze_record(source: str, record: dict[str, Any], path: Path, agg: SourceDiagnostics) -> None:
    agg.total_docs += 1
    normalized = normalize_record(record)

    title = str(record.get("title", "") or "").strip()
    content_raw = record.get("content")
    page_content_raw = record.get("page_content")
    content = extract_content(record)
    content_len = len(content)

    if title:
        agg.has_title += 1
    if isinstance(content_raw, str) and content_raw.strip():
        agg.has_content += 1
    if isinstance(page_content_raw, str) and page_content_raw.strip():
        agg.has_page_content += 1

    if normalized.source_type and normalized.source_type != "unknown":
        agg.normalized_has_source_type += 1
    if normalized.title:
        agg.normalized_has_title += 1
    if normalized.content:
        agg.normalized_has_content += 1
    if normalized.page_content:
        agg.normalized_has_page_content += 1

    agg.normalized_source_type_counts[normalized.source_type] += 1
    if normalized.source_type == "unknown":
        agg.add_sample(agg.unknown_samples, record, path)

    normalized_content_len = len(normalized.content)
    agg.content_lengths.append(normalized_content_len)

    compare_title = normalized.title or title
    compare_content = normalized.content or content
    if compare_title and compare_content and compare_title == compare_content:
        agg.title_eq_content += 1

    if source == "news":
        if compare_title == "네이버뉴스" or compare_content == "네이버뉴스":
            agg.placeholder_suspects += 1
            agg.add_sample(agg.placeholder_samples, record, path)
        if check_low_quality(source, normalized_content_len):
            agg.low_quality_suspects += 1

    elif source == "forum":
        if compare_title and compare_content and compare_title == compare_content:
            agg.add_sample(agg.title_only_samples, record, path)
        if check_low_quality(source, normalized_content_len):
            agg.low_quality_suspects += 1

    elif source == "dart":
        has_wrapper = any(token in compare_content for token in DART_WRAPPER_TOKENS)
        if has_wrapper:
            agg.dart_wrapper_suspects += 1
            agg.add_sample(agg.wrapper_samples, record, path)
            if normalized.has_body:
                agg.dart_has_body_but_wrapper += 1
        if check_low_quality(source, normalized_content_len):
            agg.low_quality_suspects += 1

    elif source == "combined":
        raw_source_type = str(record.get("source_type", "") or "unknown")
        agg.source_type_counts[raw_source_type] += 1
        if check_low_quality(source, normalized_content_len, source_type=normalized.source_type):
            agg.low_quality_suspects += 1


def analyze(paths: list[Path]) -> dict[str, SourceDiagnostics]:
    diagnostics: dict[str, SourceDiagnostics] = {}
    for path in paths:
        source = infer_source(path)
        if source is None:
            print(f"[WARN] source 추론 실패, 스킵: {path}")
            continue
        agg = diagnostics.setdefault(source, SourceDiagnostics(source=source))
        for record in parse_jsonl(path):
            analyze_record(source, record, path, agg)
    return diagnostics


def pct(part: int, total: int) -> float:
    if total == 0:
        return 0.0
    return round((part / total) * 100, 2)


def format_summary(agg: SourceDiagnostics) -> list[str]:
    stats = calc_stats(agg.content_lengths)
    lines = [
        f"총 문서 수: {agg.total_docs}",
        (
            "필드 존재 비율: "
            f"title={agg.has_title}/{agg.total_docs} ({pct(agg.has_title, agg.total_docs)}%), "
            f"content={agg.has_content}/{agg.total_docs} ({pct(agg.has_content, agg.total_docs)}%), "
            f"page_content={agg.has_page_content}/{agg.total_docs} ({pct(agg.has_page_content, agg.total_docs)}%)"
        ),
        (
            "normalized field coverage: "
            f"source_type={agg.normalized_has_source_type}/{agg.total_docs} "
            f"({pct(agg.normalized_has_source_type, agg.total_docs)}%), "
            f"title={agg.normalized_has_title}/{agg.total_docs} "
            f"({pct(agg.normalized_has_title, agg.total_docs)}%), "
            f"content={agg.normalized_has_content}/{agg.total_docs} "
            f"({pct(agg.normalized_has_content, agg.total_docs)}%), "
            f"page_content={agg.normalized_has_page_content}/{agg.total_docs} "
            f"({pct(agg.normalized_has_page_content, agg.total_docs)}%)"
        ),
        (
            "content 길이 통계: "
            f"count={stats.count}, min={stats.min}, max={stats.max}, "
            f"mean={stats.mean}, median={stats.median}, p95={stats.p95}"
        ),
        (
            f"title == content: {agg.title_eq_content}/{agg.total_docs} "
            f"({pct(agg.title_eq_content, agg.total_docs)}%)"
        ),
        f"placeholder 의심: {agg.placeholder_suspects}",
        f"low-quality 의심: {agg.low_quality_suspects}",
    ]

    if agg.source == "dart":
        lines.append(f"wrapper-text 의심: {agg.dart_wrapper_suspects}")
        lines.append(f"has_body=true + wrapper-text 의심: {agg.dart_has_body_but_wrapper}")
    if agg.source == "combined":
        lines.append(f"source_type별 문서 수: {dict(agg.source_type_counts)}")
    lines.append(f"normalized source_type별 문서 수: {dict(agg.normalized_source_type_counts)}")
    return lines


def print_samples(title: str, samples: list[dict[str, Any]]) -> None:
    print(f"\n--- {title} (최대 5건) ---")
    if not samples:
        print("(샘플 없음)")
        return
    for idx, sample in enumerate(samples, 1):
        print(
            f"[{idx}] path={sample['path']} | source_type={sample.get('source_type')} | "
            f"title={sample.get('title')} | content={sample.get('content')}"
        )


def make_json_report(diagnostics: dict[str, SourceDiagnostics]) -> dict[str, Any]:
    report: dict[str, Any] = {}
    for source, agg in diagnostics.items():
        stats = calc_stats(agg.content_lengths)
        report[source] = {
            "source": source,
            "total_docs": agg.total_docs,
            "presence": {
                "title": agg.has_title,
                "content": agg.has_content,
                "page_content": agg.has_page_content,
            },
            "normalized_presence": {
                "source_type": agg.normalized_has_source_type,
                "title": agg.normalized_has_title,
                "content": agg.normalized_has_content,
                "page_content": agg.normalized_has_page_content,
            },
            "title_eq_content": {
                "count": agg.title_eq_content,
                "ratio_percent": pct(agg.title_eq_content, agg.total_docs),
            },
            "content_length_stats": asdict(stats),
            "placeholder_suspects": agg.placeholder_suspects,
            "low_quality_suspects": agg.low_quality_suspects,
            "dart_wrapper_suspects": agg.dart_wrapper_suspects,
            "dart_has_body_but_wrapper": agg.dart_has_body_but_wrapper,
            "source_type_counts": dict(agg.source_type_counts),
            "normalized_source_type_counts": dict(agg.normalized_source_type_counts),
            "samples": {
                "news_placeholder": agg.placeholder_samples,
                "forum_title_only": agg.title_only_samples,
                "dart_wrapper": agg.wrapper_samples,
                "unknown_unparsed": agg.unknown_samples,
            },
        }
    return report


def print_report(diagnostics: dict[str, SourceDiagnostics]) -> None:
    if not diagnostics:
        print("진단 가능한 JSONL이 없습니다.")
        return

    for source in sorted(diagnostics):
        agg = diagnostics[source]
        print(f"\n================= SOURCE: {source} =================")
        for line in format_summary(agg):
            print(f"- {line}")

    print_samples("news placeholder 샘플", diagnostics.get("news", SourceDiagnostics("news")).placeholder_samples)
    print_samples("forum title-only 샘플", diagnostics.get("forum", SourceDiagnostics("forum")).title_only_samples)
    print_samples("dart wrapper-text 샘플", diagnostics.get("dart", SourceDiagnostics("dart")).wrapper_samples)
    print_samples(
        "unknown/unparsed sample",
        [sample for agg in diagnostics.values() for sample in agg.unknown_samples][:5],
    )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    paths = discover_jsonl_paths(args.inputs)
    if not paths:
        print("[ERROR] 진단할 JSONL 파일을 찾지 못했습니다.")
        return 1

    diagnostics = analyze(paths)
    print_report(diagnostics)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        report_obj = make_json_report(diagnostics)
        with args.json_out.open("w", encoding="utf-8") as handle:
            json.dump(report_obj, handle, ensure_ascii=False, indent=2)
        print(f"\n[INFO] JSON 리포트 저장 완료: {args.json_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
