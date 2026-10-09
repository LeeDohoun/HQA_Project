#!/usr/bin/env python3
"""Opt-in live checks against the KIS paper (모의투자) API, sending the backend's requests.

RUN_KIS_LIVE_TESTS=1 runs the read-only checks: a token and a current price (these need only
KIS_PAPER_APP_KEY and KIS_PAPER_APP_SECRET), then the balance, the orderable amount and today's
orders (these also need KIS_PAPER_ACCOUNT_NO). RUN_KIS_ORDER_TEST=1 additionally places one
1-share limit buy about 10% under the market and cancels it, with the backend's order and cancel
codes. Request fields and tr_ids follow backend/.../service/KisClient.java, so a pass here means
the backend's calls are accepted. Nothing prints the key, secret, token or account number.

Run directly for a short report: ``python tests/test_kis_paper_trading.py [--order]``.
"""

import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

import pytest
import requests


DOMAIN = "https://openapivts.koreainvestment.com:29443"
KEY = os.getenv("KIS_PAPER_APP_KEY", "").strip()
SECRET = os.getenv("KIS_PAPER_APP_SECRET", "").strip()
ACCOUNT = os.getenv("KIS_PAPER_ACCOUNT_NO", "").strip().replace("-", "")
KST = timezone(timedelta(hours=9))
_last_call = [0.0]


def account_parts():
    """(CANO, ACNT_PRDT_CD) from KIS_PAPER_ACCOUNT_NO, or None when it is not set.

    Accepts 12345678, 12345678-01 and 1234567801 (8 digits alone get product code 01), as the
    backend does. Any other value is a configuration error, not a reason to skip.
    """
    if not ACCOUNT:
        return None
    if not ACCOUNT.isdigit() or len(ACCOUNT) not in (8, 10):
        raise ValueError(f"KIS_PAPER_ACCOUNT_NO must be 8 digits or 8-2 digits; it has {len(ACCOUNT)} characters")
    return ACCOUNT[:8], ACCOUNT[8:] or "01"


def _paced():
    # The paper API allows about one request per second per app key.
    wait = 1.1 - (time.monotonic() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    _last_call[0] = time.monotonic()


def refusal(data):
    """What KIS said, without credentials."""
    return f"rt_cd={data.get('rt_cd')} msg_cd={data.get('msg_cd')} msg1={data.get('msg1')}"


def issue_token():
    """(token, None) or ("", what KIS answered). KIS issues one token per minute per app key
    (EGW00133), so a run right after another waits that minute out once."""
    for attempt in (1, 2):
        _paced()
        resp = requests.post(
            f"{DOMAIN}/oauth2/tokenP",
            json={"grant_type": "client_credentials", "appkey": KEY, "appsecret": SECRET},
            timeout=15,
        )
        data = resp.json()
        token = data.get("access_token", "")
        if token:
            return token, None
        if data.get("error_code") != "EGW00133" or attempt == 2:
            return "", {k: data.get(k) for k in ("error_code", "error_description", "msg_cd", "msg1") if data.get(k)}
        time.sleep(61)


def base_headers(token, tr_id):
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": KEY,
        "appsecret": SECRET,
        "tr_id": tr_id,
        "custtype": "P",
    }


def _get(token, path, tr_id, params):
    _paced()
    resp = requests.get(f"{DOMAIN}{path}", headers=base_headers(token, tr_id), params=params, timeout=15)
    return resp.json()


def _post(token, path, tr_id, body):
    _paced()
    resp = requests.post(f"{DOMAIN}{path}", headers=base_headers(token, tr_id), json=body, timeout=15)
    return resp.json()


def inquire_price(token, stock_code="005930"):
    return _get(token, "/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code})


def inquire_balance(token, cano, product):
    return _get(token, "/uapi/domestic-stock/v1/trading/inquire-balance", "VTTC8434R", {
        "CANO": cano, "ACNT_PRDT_CD": product, "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
        "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
        "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
    })


def inquire_orderable(token, cano, product, stock_code, price):
    return _get(token, "/uapi/domestic-stock/v1/trading/inquire-psbl-order", "VTTC8908R", {
        "CANO": cano, "ACNT_PRDT_CD": product, "PDNO": stock_code, "ORD_UNPR": str(price), "ORD_DVSN": "01",
        "CMA_EVLU_AMT_ICLD_YN": "N", "OVRS_ICLD_YN": "N",
    })


def inquire_orders(token, cano, product, day):
    ymd = day.strftime("%Y%m%d")
    return _get(token, "/uapi/domestic-stock/v1/trading/inquire-daily-ccld", "VTTC0081R", {
        "CANO": cano, "ACNT_PRDT_CD": product, "INQR_STRT_DT": ymd, "INQR_END_DT": ymd, "SLL_BUY_DVSN_CD": "00",
        "CCLD_DVSN": "00", "INQR_DVSN": "00", "INQR_DVSN_3": "00", "PDNO": "", "ORD_GNO_BRNO": "", "ODNO": "",
        "INQR_DVSN_1": "", "EXCG_ID_DVSN_CD": "KRX", "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
    })


def place_order(token, cano, product, stock_code, price):
    """One-share limit buy with the backend's paper order code."""
    return _post(token, "/uapi/domestic-stock/v1/trading/order-cash", "VTTC0012U", {
        "CANO": cano, "ACNT_PRDT_CD": product, "PDNO": stock_code, "ORD_DVSN": "00",
        "ORD_QTY": "1", "ORD_UNPR": str(price),
    })


def cancel_order(token, cano, product, order_no, organization):
    """Cancel the whole remainder, with the backend's paper revise/cancel code."""
    return _post(token, "/uapi/domestic-stock/v1/trading/order-rvsecncl", "VTTC0013U", {
        "CANO": cano, "ACNT_PRDT_CD": product, "KRX_FWDG_ORD_ORGNO": organization, "ORGN_ODNO": order_no,
        "ORD_DVSN": "00", "RVSE_CNCL_DVSN_CD": "02", "ORD_QTY": "1", "ORD_UNPR": "0",
        "QTY_ALL_ORD_YN": "Y", "EXCG_ID_DVSN_CD": "KRX",
    })


def krx_tick(price):
    """KRX price unit (2023 table, KOSPI and KOSDAQ alike)."""
    for limit, tick in ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500)):
        if price < limit:
            return tick
    return 1_000


def below_market(current):
    """About 10% under the current price, on the tick grid and inside the -30% daily limit."""
    target = current * 0.9
    tick = krx_tick(target)
    return int(math.floor(target / tick) * tick)


@pytest.fixture(scope="module")
def token():
    if os.getenv("RUN_KIS_LIVE_TESTS") != "1":
        pytest.skip("set RUN_KIS_LIVE_TESTS=1 to run live KIS paper checks")
    if not KEY or not SECRET:
        pytest.skip("KIS_PAPER_APP_KEY and KIS_PAPER_APP_SECRET are not configured")
    issued, detail = issue_token()
    assert issued, f"KIS paper token was not issued: {detail}"
    return issued


@pytest.fixture(scope="module")
def account(token):
    parts = account_parts()
    if parts is None:
        pytest.skip("KIS_PAPER_ACCOUNT_NO is not configured")
    return parts


def test_price(token):
    data = inquire_price(token)
    assert data.get("rt_cd") == "0", refusal(data)
    assert int((data.get("output") or {}).get("stck_prpr") or 0) > 0


def test_balance(token, account):
    data = inquire_balance(token, *account)
    assert data.get("rt_cd") == "0", refusal(data)
    assert isinstance(data.get("output1"), list) and data.get("output2"), "balance rows missing"


def test_orderable_amount(token, account):
    current = int(inquire_price(token)["output"]["stck_prpr"])
    data = inquire_orderable(token, *account, "005930", current)
    assert data.get("rt_cd") == "0", refusal(data)
    assert "nrcvb_buy_amt" in (data.get("output") or {}) and "nrcvb_buy_qty" in data["output"]


def test_order_history(token, account):
    data = inquire_orders(token, *account, datetime.now(KST))
    assert data.get("rt_cd") == "0", refusal(data)
    assert isinstance(data.get("output1"), list)


@pytest.fixture(scope="module")
def order_enabled():
    if os.getenv("RUN_KIS_ORDER_TEST") != "1":
        pytest.skip("set RUN_KIS_ORDER_TEST=1 to place and cancel one paper order")


def test_order_and_cancel(order_enabled, token, account):
    current = int(inquire_price(token)["output"]["stck_prpr"])
    order = place_order(token, *account, "005930", below_market(current))
    assert order.get("rt_cd") == "0", refusal(order)
    output = order.get("output") or {}
    cancel = cancel_order(token, *account, output.get("ODNO", ""), output.get("KRX_FWDG_ORD_ORGNO", ""))
    assert cancel.get("rt_cd") == "0", f"order {output.get('ODNO')} may still be working: {refusal(cancel)}"


def main():
    print("KIS 모의투자 API 점검 (키·토큰·계좌번호는 출력하지 않습니다)")
    print(f"  앱키: {'설정됨' if KEY else '없음'}, 시크릿: {'설정됨' if SECRET else '없음'}, 계좌번호: {len(ACCOUNT)}자리")
    if not KEY or not SECRET:
        print("[중단] KIS_PAPER_APP_KEY와 KIS_PAPER_APP_SECRET을 설정하세요.")
        return 1
    token, detail = issue_token()
    print(f"[1] 토큰 발급: {'성공' if token else f'실패 {detail}'}")
    if not token:
        return 1
    price = inquire_price(token)
    current = int((price.get("output") or {}).get("stck_prpr") or 0)
    print(f"[2] 현재가(005930): {current:,}원" if price.get("rt_cd") == "0" else f"[2] 현재가 실패: {refusal(price)}")
    try:
        account = account_parts()
    except ValueError as exc:
        print(f"[3] 계좌번호 오류: {exc}")
        return 1
    if account is None:
        print("[3] 계좌번호가 없어 잔고·주문 조회를 건너뜁니다.")
        return 0 if current else 1
    balance = inquire_balance(token, *account)
    ok = balance.get("rt_cd") == "0"
    summary = (balance.get("output2") or [{}])[0] if ok else {}
    print(f"[3] 잔고: 예수금 {summary.get('dnca_tot_amt')}원, 총평가 {summary.get('tot_evlu_amt')}원" if ok
          else f"[3] 잔고 실패: {refusal(balance)}")
    orders = inquire_orders(token, *account, datetime.now(KST))
    print(f"[4] 당일 주문 {len(orders.get('output1') or [])}건" if orders.get("rt_cd") == "0"
          else f"[4] 주문 조회 실패: {refusal(orders)}")
    if "--order" in sys.argv[1:] and ok and current:
        price_limit = below_market(current)
        order = place_order(token, *account, "005930", price_limit)
        if order.get("rt_cd") != "0":
            print(f"[5] 주문 실패: {refusal(order)}")
            return 1
        output = order.get("output") or {}
        cancel = cancel_order(token, *account, output.get("ODNO", ""), output.get("KRX_FWDG_ORD_ORGNO", ""))
        print(f"[5] 1주 {price_limit:,}원 매수 주문 → 취소 {'성공' if cancel.get('rt_cd') == '0' else f'실패 {refusal(cancel)}'}")
    return 0 if ok and current else 1


if __name__ == "__main__":
    sys.exit(main())
