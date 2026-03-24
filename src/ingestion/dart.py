from __future__ import annotations

from typing import List

from .base import BaseCollector
from .types import DocumentRecord


class DartDisclosureCollector(BaseCollector):
    LIST_URL = "https://opendart.fss.or.kr/api/list.json"

    def __init__(self, api_key: str, timeout: int = 20):
        super().__init__(timeout=timeout)
        self.api_key = api_key

    def collect(
        self,
        corp_code: str,
        bgn_de: str,
        end_de: str,
        page_count: int = 100,
    ) -> List[DocumentRecord]:
        response = self.get_with_retry(
            self.LIST_URL,
            params={
                "crtfc_key": self.api_key,
                "corp_code": corp_code,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "page_count": page_count,
            },
            timeout=self.timeout,
            log_prefix=f"DART:{corp_code}",
        )
        payload = response.json()

        if payload.get("status") != "000":
            return []

        docs: List[DocumentRecord] = []
        for item in payload.get("list", []):
            title = item.get("report_nm", "")
            rcept_no = item.get("rcept_no", "")
            url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else ""
            published_at = self.to_iso_datetime(item.get("rcept_dt", ""), ["%Y%m%d"], default_time="00:00:00")
            docs.append(
                DocumentRecord(
                    source_type="dart",
                    title=title,
                    content=f"{item.get('corp_name', '')} 공시: {title}",
                    url=url,
                    stock_code=item.get("stock_code"),
                    published_at=published_at,
                    metadata={
                        "corp_name": item.get("corp_name", ""),
                        "flr_nm": item.get("flr_nm", ""),
                        "rcept_no": rcept_no,
                    },
                )
            )

        return docs
