from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from .base import BaseCollector
from .types import FinancialSnapshot


class DartFinancialStatementCollector(BaseCollector):
    """OpenDART 단일회사 주요계정 기반 재무 스냅샷 수집기."""

    URL = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"
    ANNUAL_REPORT_CODE = "11011"
    REPORT_NAMES = {
        "11011": "사업보고서",
        "11012": "반기보고서",
        "11013": "1분기보고서",
        "11014": "3분기보고서",
    }

    ACCOUNT_ALIASES = {
        "revenue": ("매출액", "수익(매출액)", "영업수익"),
        "operating_profit": ("영업이익", "영업이익(손실)"),
        "net_income": ("당기순이익", "당기순이익(손실)", "연결당기순이익"),
        "assets": ("자산총계", "자산 총계"),
        "liabilities": ("부채총계", "부채 총계"),
        "equity": ("자본총계", "자본 총계"),
        "current_assets": ("유동자산", "유동 자산"),
        "current_liabilities": ("유동부채", "유동 부채"),
    }

    def __init__(self, api_key: str | None = None, timeout: int = 20):
        super().__init__(timeout=timeout)
        self.api_key = (api_key or "").strip()

    def collect_latest_annual(
        self,
        stock_name: str,
        stock_code: str,
        corp_code: str,
        from_date: str,
        to_date: str,
    ) -> Optional[FinancialSnapshot]:
        if not self.api_key or not corp_code:
            return None

        from_year = self._year(from_date, default=datetime.utcnow().year - 2)
        to_year = self._year(to_date, default=datetime.utcnow().year)

        for year in range(to_year, from_year - 1, -1):
            snapshot = self.collect_annual(
                stock_name=stock_name,
                stock_code=stock_code,
                corp_code=corp_code,
                fiscal_year=str(year),
            )
            if snapshot is not None:
                return snapshot
        return None

    def collect_annual(
        self,
        stock_name: str,
        stock_code: str,
        corp_code: str,
        fiscal_year: str,
    ) -> Optional[FinancialSnapshot]:
        rows = self._fetch_rows(corp_code, fiscal_year, self.ANNUAL_REPORT_CODE)
        if not rows:
            return None

        rows = self._prefer_consolidated(rows)
        account_values = self._extract_accounts(rows)

        revenue = account_values.get("revenue")
        operating_profit = account_values.get("operating_profit")
        net_income = account_values.get("net_income")
        assets = account_values.get("assets")
        liabilities = account_values.get("liabilities")
        equity = account_values.get("equity")
        current_assets = account_values.get("current_assets")
        current_liabilities = account_values.get("current_liabilities")

        return FinancialSnapshot(
            source_type="financials",
            stock_name=stock_name,
            stock_code=stock_code,
            corp_code=corp_code,
            fiscal_year=fiscal_year,
            report_code=self.ANNUAL_REPORT_CODE,
            report_name=self.REPORT_NAMES[self.ANNUAL_REPORT_CODE],
            revenue=revenue,
            operating_profit=operating_profit,
            net_income=net_income,
            assets=assets,
            liabilities=liabilities,
            equity=equity,
            roe=self._ratio(net_income, equity),
            roa=self._ratio(net_income, assets),
            operating_margin=self._ratio(operating_profit, revenue),
            net_margin=self._ratio(net_income, revenue),
            debt_ratio=self._ratio(liabilities, equity),
            current_assets=current_assets,
            current_liabilities=current_liabilities,
            current_ratio=self._ratio(current_assets, current_liabilities),
            currency=self._currency(rows),
            as_of=self._as_of(rows),
            metadata={
                "source": "dart",
                "report_code": self.ANNUAL_REPORT_CODE,
                "quality_status": self._quality_status(account_values),
                "missing_fields": ",".join(
                    key for key in self.ACCOUNT_ALIASES if account_values.get(key) is None
                ),
            },
        )

    def _fetch_rows(self, corp_code: str, fiscal_year: str, report_code: str) -> List[Dict[str, Any]]:
        response = self.get_with_retry(
            self.URL,
            params={
                "crtfc_key": self.api_key,
                "corp_code": corp_code,
                "bsns_year": fiscal_year,
                "reprt_code": report_code,
            },
            timeout=self.timeout,
            log_prefix=f"DART:FINANCIALS:{corp_code}:{fiscal_year}",
        )
        payload = response.json()
        if payload.get("status") != "000":
            return []
        rows = payload.get("list") or []
        return rows if isinstance(rows, list) else []

    @staticmethod
    def _prefer_consolidated(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        consolidated = [row for row in rows if str(row.get("fs_div", "")).strip() == "CFS"]
        return consolidated or rows

    def _extract_accounts(self, rows: Iterable[Dict[str, Any]]) -> Dict[str, Optional[float]]:
        values: Dict[str, Optional[float]] = {key: None for key in self.ACCOUNT_ALIASES}
        for row in rows:
            account_name = self._normalize_account_name(str(row.get("account_nm", "")))
            for key, aliases in self.ACCOUNT_ALIASES.items():
                if values[key] is not None:
                    continue
                if any(self._normalize_account_name(alias) == account_name for alias in aliases):
                    values[key] = self._amount(row)
        return values

    @staticmethod
    def _normalize_account_name(value: str) -> str:
        return value.replace(" ", "").replace("\u3000", "").strip()

    @staticmethod
    def _amount(row: Dict[str, Any]) -> Optional[float]:
        for key in ("thstrm_amount", "thstrm_add_amount"):
            raw = row.get(key)
            if raw not in (None, "", "-"):
                try:
                    return float(str(raw).replace(",", "").strip())
                except ValueError:
                    continue
        return None

    @staticmethod
    def _ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
        if numerator is None or denominator in (None, 0):
            return None
        return round((numerator / denominator) * 100, 2)

    @staticmethod
    def _currency(rows: List[Dict[str, Any]]) -> str:
        for row in rows:
            currency = str(row.get("currency", "")).strip()
            if currency:
                return currency
        return "KRW"

    @staticmethod
    def _as_of(rows: List[Dict[str, Any]]) -> Optional[str]:
        for row in rows:
            value = str(row.get("thstrm_dt", "")).strip()
            if value:
                return value
        return None

    @staticmethod
    def _quality_status(values: Dict[str, Optional[float]]) -> str:
        required = ("revenue", "operating_profit", "net_income", "liabilities", "equity")
        missing = [key for key in required if values.get(key) is None]
        if not missing:
            return "complete"
        if len(missing) <= 2:
            return "partial"
        return "insufficient"

    @staticmethod
    def _year(value: str, default: int) -> int:
        text = str(value or "").strip()
        if len(text) >= 4 and text[:4].isdigit():
            return int(text[:4])
        return default
