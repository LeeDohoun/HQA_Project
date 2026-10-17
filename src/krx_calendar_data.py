"""Official-notice data; mechanical updates are separate from calendar algorithms."""
import exchange_calendars as calendars

CALENDAR_VERSION = "exchange-calendars:" + calendars.__version__ + ":XKRX:krx-notices-through-2026-v3"
# KRX announcement dated 2026-05-20, supplied and verified for this project.
# One project override shared by research, forward and price validation.
KRX_ADDITIONAL_CLOSED_DAYS = {
    "2026-06-03": {
        "reason": "Local election day; KRX all-market closure",
        "source": "https://news.bizwatch.co.kr/article/market/2026/05/20/0023",
    },
    "2026-07-17": {
        "reason": "Constitution Day restored as a statutory holiday in 2026; KRX all-market closure",
        "source": "https://news.bizwatch.co.kr/article/market/2026/05/20/0023",
    },
}
CALENDAR_VERIFIED_THROUGH = "2026-12-31"


# Days that may have special KRX hours but whose official notice is not yet
# recorded in SPECIAL_CLOSES. Only these fail closed; other verified-year days
# use the dependency's regular schedule. 2026-11-19 is the 2027 CSAT
# (Ministry of Education, announced 2024-08-16); KRX usually publishes the
# changed hours about two weeks before. Move it to SPECIAL_CLOSES with the
# official notice when published.
PENDING_SPECIAL_SESSIONS = {
    "2026-11-19": "2027학년도 대학수학능력시험; KRX special-hours notice pending",
}
SPECIAL_CLOSES = {
    "2021-11-18": {
        "close": "2021-11-18T16:30:00+09:00", "published_at": "2021-11-04T10:00:00+09:00",
        "source_urls": {
            "KOSPI": "https://kind.krx.co.kr/external/2021/11/04/000107/20211104000121/99303.htm",
            "KOSDAQ": "https://kind.krx.co.kr/external/2021/11/04/000104/20211101000204/70780.htm",
        },
    },
    "2022-11-17": {
        "close": "2022-11-17T16:30:00+09:00", "published_at": "2022-11-03T10:00:00+09:00",
        "source_urls": {
            "KOSPI": "https://kind.krx.co.kr/external/2022/11/03/000103/20221102001061/99303.htm",
            "KOSDAQ": "https://kind.krx.co.kr/external/2022/11/03/000105/20221019000846/70780.htm",
        },
    },
    "2023-11-16": {
        "close": "2023-11-16T16:30:00+09:00", "published_at": "2023-11-02T10:00:00+09:00",
        "source_urls": {
            "KOSPI": "https://kind.krx.co.kr/external/2023/11/02/000068/20231102000001/99303.htm",
            "KOSDAQ": "https://kind.krx.co.kr/external/2023/11/02/000075/20231101001384/70780.htm",
        },
    },
    "2024-11-14": {
        "close": "2024-11-14T16:30:00+09:00", "published_at": "2024-10-31T10:00:00+09:00",
        "source_urls": {
            "KOSPI": "https://kind.krx.co.kr/external/2024/10/31/000086/20241031000185/99303.htm",
            "KOSDAQ": "https://kind.krx.co.kr/external/2024/10/31/000078/20241021000338/70780.htm",
        },
    },
    "2025-11-13": {
        "close": "2025-11-13T16:30:00+09:00", "published_at": "2025-10-30T10:00:00+09:00",
        "source_urls": {
            "KOSPI": "https://kind.krx.co.kr/external/2025/10/30/000102/20251030000137/99303.htm",
            "KOSDAQ": "https://kind.krx.co.kr/external/2025/10/30/000121/20251021000455/70780.htm",
        },
    },
}

