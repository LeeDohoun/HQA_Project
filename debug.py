from dotenv import load_dotenv
load_dotenv()

# File role:
# - Manual DART collector smoke-check.
# - Useful when verifying body extraction quality outside the full pipeline.

import os
from src.ingestion.dart import DartDisclosureCollector

api_key = os.getenv("DART_API_KEY", "").strip()
if not api_key:
    raise ValueError("DART_API_KEY가 없습니다.")

collector = DartDisclosureCollector(api_key=api_key)

rows = collector.collect(
    corp_code="00126362",   # 삼성SDI
    bgn_de="20240101",
    end_de="20241231",
    page_count=30,
)

print("수집 건수 =", len(rows))

for i, row in enumerate(rows[:5], start=1):
    print(f"\n[{i}]")
    print("title       =", row.title)
    print("url         =", row.url)
    print("published_at=", row.published_at)
    print("content     =", row.content[:500])
    print("metadata    =", row.metadata)
