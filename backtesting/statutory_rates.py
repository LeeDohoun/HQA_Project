"""Legally sourced sell-tax data; cost algorithms remain pinned separately."""
from datetime import date

# Combined securities transaction tax; KOSPI includes the rural special tax.
# Keep start dates sorted and verify against the statute when extending this table.
SELL_TAX = (
    (date(2015, 1, 1), {"KOSPI": 0.0030, "KOSDAQ": 0.0030}),
    (date(2019, 6, 3), {"KOSPI": 0.0025, "KOSDAQ": 0.0025}),
    (date(2021, 1, 1), {"KOSPI": 0.0023, "KOSDAQ": 0.0023}),
    (date(2023, 1, 1), {"KOSPI": 0.0020, "KOSDAQ": 0.0020}),
    (date(2024, 1, 1), {"KOSPI": 0.0018, "KOSDAQ": 0.0018}),
    (date(2025, 1, 1), {"KOSPI": 0.0015, "KOSDAQ": 0.0015}),
    (date(2026, 1, 1), {"KOSPI": 0.0020, "KOSDAQ": 0.0020}),
)
# TODO FW001: add the 2027 sell/settlement rate only with a legal source URL,
# effective date and recorded amendment hashes. Unknown sell years must fail.

