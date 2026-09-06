#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path
from zipfile import BadZipFile, ZipFile

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config.settings import load_project_env

load_project_env()


CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"


def _extract_corp_code_xml(zip_bytes: bytes) -> bytes:
    with ZipFile(BytesIO(zip_bytes)) as zf:
        xml_names = [name for name in zf.namelist() if name.lower().endswith(".xml")]
        if not xml_names:
            raise ValueError("DART corpCode 응답 ZIP 안에 XML 파일이 없습니다.")
        return zf.read(xml_names[0])


def _parse_corp_codes(xml_bytes: bytes, *, include_unlisted: bool = False) -> list[dict[str, str]]:
    root = ET.fromstring(xml_bytes)
    rows: list[dict[str, str]] = []

    for item in root.findall(".//list"):
        corp_code = (item.findtext("corp_code") or "").strip()
        corp_name = (item.findtext("corp_name") or "").strip()
        stock_code = (item.findtext("stock_code") or "").strip()
        modify_date = (item.findtext("modify_date") or "").strip()

        if not re.fullmatch(r"[0-9]{8}", corp_code) or (stock_code and not re.fullmatch(r"[0-9]{6}", stock_code)):
            raise ValueError("DART corpCode contains invalid corporate or stock code")
        if not include_unlisted and not stock_code:
            continue

        rows.append(
            {
                "stock_code": stock_code,
                "corp_code": corp_code,
                "corp_name": corp_name,
                "modify_date": modify_date,
            }
        )

    if not rows:
        raise ValueError("DART corpCode contains no eligible records")
    listed = {}
    for row in rows:
        stock = row["stock_code"]
        if stock and stock in listed and listed[stock] != row["corp_code"]:
            raise ValueError(f"DART corpCode contains conflicting mapping for stock:{stock}")
        listed[stock] = row["corp_code"]
    rows.sort(key=lambda row: (row["stock_code"] or "999999", row["corp_name"]))
    return rows


def _write_csv(rows: list[dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["stock_code", "corp_code", "corp_name", "modify_date"]

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8-sig",
        newline="",
        delete=False,
        dir=str(output_path.parent),
    ) as tmp:
        writer = csv.DictWriter(tmp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        temp_path = Path(tmp.name)

    temp_path.replace(output_path)


def download_corp_codes(api_key: str, *, include_unlisted: bool = False) -> list[dict[str, str]]:
    if not api_key or not api_key.strip():
        raise ValueError("DART_API_KEY is required")
    try:
        response = requests.get(CORP_CODE_URL, params={"crtfc_key": api_key}, timeout=60, allow_redirects=False)
        if not 200 <= response.status_code < 300:
            raise ValueError(f"DART corpCode HTTP status={response.status_code}")
    except requests.RequestException:
        raise ValueError("DART corpCode transport failure") from None
    if not response.content.startswith(b"PK"):
        try:
            status = ET.fromstring(response.content).findtext("status")
        except ET.ParseError:
            status = None
        if status and re.fullmatch(r"[0-9]{3}", status):
            raise ValueError(f"DART corpCode provider error status={status}")
        raise ValueError("DART corpCode response is not a ZIP archive")
    try:
        xml_bytes = _extract_corp_code_xml(response.content)
        return _parse_corp_codes(xml_bytes, include_unlisted=include_unlisted)
    except (BadZipFile, ET.ParseError):
        raise ValueError("DART corpCode malformed archive or XML") from None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OpenDART corpCode.xml을 내려받아 theme_pipeline용 corp_codes.csv를 생성합니다.",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("DART_API_KEY", ""),
        help="OpenDART API key. 생략하면 DART_API_KEY 환경변수를 사용합니다.",
    )
    parser.add_argument(
        "--output",
        default="./corp_codes.csv",
        help="생성할 CSV 경로. 기본값: ./corp_codes.csv",
    )
    parser.add_argument(
        "--include-unlisted",
        action="store_true",
        help="stock_code가 없는 비상장 법인도 CSV에 포함합니다.",
    )
    args = parser.parse_args()

    api_key = (args.api_key or "").strip()
    if not api_key:
        parser.error("DART_API_KEY가 필요합니다. .env에 넣거나 --api-key로 전달하세요.")

    output_path = Path(args.output)
    rows = download_corp_codes(api_key, include_unlisted=args.include_unlisted)
    _write_csv(rows, output_path)

    listed_count = sum(1 for row in rows if row["stock_code"])
    print(f"[DART] saved {len(rows)} rows to {output_path}")
    print(f"[DART] listed rows={listed_count}")


if __name__ == "__main__":
    main()
