#!/usr/bin/env python3
"""KIS 모의투자 API 연결 테스트 스크립트"""

import os
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)

import requests


DOMAIN = "https://openapivts.koreainvestment.com:29443"
KEY = os.getenv("KIS_PAPER_APP_KEY", "")
SECRET = os.getenv("KIS_PAPER_APP_SECRET", "")
ACCT = os.getenv("KIS_PAPER_ACCOUNT_NO", "").replace("-", "")
CANO = ACCT[:8] if len(ACCT) >= 8 else ""
PRDT = ACCT[8:10] if len(ACCT) >= 10 else "01"


def issue_token():
    """토큰 발급"""
    resp = requests.post(
        f"{DOMAIN}/oauth2/tokenP",
        json={"grant_type": "client_credentials", "appkey": KEY, "appsecret": SECRET},
        timeout=15,
    )
    data = resp.json()
    token = data.get("access_token", "")
    if not token:
        print(f"[FAIL] 토큰 발급 실패: {data}")
        return ""
    print(f"[OK] 토큰 발급 성공 (앞 30자: {token[:30]}...)")
    return token


def base_headers(token, tr_id):
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": KEY,
        "appsecret": SECRET,
        "tr_id": tr_id,
        "custtype": "P",
    }


def test_balance(token):
    """잔고 조회"""
    h = base_headers(token, "VTTC8434R")
    p = {
        "CANO": CANO, "ACNT_PRDT_CD": PRDT,
        "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
        "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
        "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
    }
    resp = requests.get(
        f"{DOMAIN}/uapi/domestic-stock/v1/trading/inquire-balance",
        headers=h, params=p, timeout=15,
    )
    d = resp.json()
    rt_cd = d.get("rt_cd")
    msg1 = d.get("msg1", "")
    print(f"  rt_cd={rt_cd}, msg1={msg1}")

    if rt_cd == "0":
        out2 = d.get("output2", [])
        if out2:
            print(f"  예수금: {out2[0].get('dnca_tot_amt', '?')}원")
            print(f"  총 평가: {out2[0].get('tot_evlu_amt', '?')}원")
        return True
    return False


def test_price(token, stock_code="005930"):
    """현재가 조회"""
    h = base_headers(token, "FHKST01010100")
    p = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code}
    resp = requests.get(
        f"{DOMAIN}/uapi/domestic-stock/v1/quotations/inquire-price",
        headers=h, params=p, timeout=15,
    )
    d = resp.json()
    rt_cd = d.get("rt_cd")
    if rt_cd == "0":
        out = d.get("output", {})
        name = out.get("hts_kor_isnm", "")
        price = out.get("stck_prpr", "0")
        print(f"  {name}: {price}원")
        return int(price)
    else:
        print(f"  조회 실패: {d.get('msg1', '')}")
        return 0


def test_order(token, stock_code="005930", price=50000):
    """모의투자 매수 주문 테스트"""
    body = {
        "CANO": CANO, "ACNT_PRDT_CD": PRDT,
        "PDNO": stock_code, "ORD_DVSN": "00",
        "ORD_QTY": "1", "ORD_UNPR": str(price),
        "CTAC_TLNO": "", "SLL_TYPE": "01", "ALGO_NO": "",
    }

    # hashkey 생성
    hr = requests.post(
        f"{DOMAIN}/uapi/hashkey",
        headers={
            "content-type": "application/json; charset=utf-8",
            "appkey": KEY, "appsecret": SECRET,
        },
        json=body, timeout=10,
    )
    hk = hr.json().get("HASH", "") or hr.json().get("hash", "")
    print(f"  hashkey: {'OK' if hk else 'FAIL'}")

    h = base_headers(token, "VTTC0802U")
    if hk:
        h["hashkey"] = hk

    resp = requests.post(
        f"{DOMAIN}/uapi/domestic-stock/v1/trading/order-cash",
        headers=h, json=body, timeout=15,
    )
    d = resp.json()
    rt_cd = d.get("rt_cd")
    msg_cd = d.get("msg_cd", "")
    msg1 = d.get("msg1", "")
    print(f"  rt_cd={rt_cd}, msg_cd={msg_cd}")
    print(f"  msg1={msg1}")

    if rt_cd == "0":
        odno = (d.get("output") or {}).get("ODNO", "")
        print(f"  ✅ 주문 성공! 주문번호: {odno}")
        return odno
    else:
        print(f"  ❌ 주문 실패")
        return ""


def cancel_order(token, order_no, stock_code="005930", price=50000):
    """주문 취소"""
    body = {
        "CANO": CANO, "ACNT_PRDT_CD": PRDT,
        "KRX_FWDG_ORD_ORGNO": "", "ORGN_ODNO": str(order_no),
        "ORD_DVSN": "00", "RVSE_CNCL_DVSN_CD": "02",
        "ORD_QTY": "1", "ORD_UNPR": str(price),
        "QTY_ALL_ORD_YN": "Y",
    }
    h = base_headers(token, "VTTC0803U")
    resp = requests.post(
        f"{DOMAIN}/uapi/domestic-stock/v1/trading/order-rvsecncl",
        headers=h, json=body, timeout=15,
    )
    d = resp.json()
    print(f"  취소 rt_cd={d.get('rt_cd')}, msg1={d.get('msg1', '')}")


def main():
    print("=" * 50)
    print("KIS 모의투자 API 종합 테스트")
    print("=" * 50)
    print()

    # 설정 확인
    print("[설정 확인]")
    print(f"  APP_KEY: {'OK' if KEY else 'MISSING'} (길이: {len(KEY)})")
    print(f"  APP_SECRET: {'OK' if SECRET else 'MISSING'} (길이: {len(SECRET)})")
    print(f"  CANO: {CANO}")
    print(f"  ACNT_PRDT_CD: {PRDT}")
    print()

    if not KEY or not SECRET or not CANO:
        print("[FAIL] KIS 모의투자 설정이 불완전합니다.")
        return

    # 1. 토큰 발급
    print("[1] 토큰 발급")
    token = issue_token()
    if not token:
        return
    print()

    # 2. 잔고 조회
    print("[2] 잔고 조회")
    balance_ok = test_balance(token)
    print()

    # 3. 현재가 조회
    print("[3] 현재가 조회 (삼성전자)")
    current_price = test_price(token)
    print()

    # 4. 주문 테스트 (현재가 보다 낮은 가격으로)
    test_price_val = max(current_price - 5000, 50000) if current_price > 0 else 50000
    print(f"[4] 매수 주문 테스트 (삼성전자 1주 × {test_price_val}원)")
    odno = test_order(token, price=test_price_val)
    print()

    # 5. 주문 취소
    if odno:
        print(f"[5] 주문 취소 (주문번호: {odno})")
        cancel_order(token, odno, price=test_price_val)
        print()

    # 결과 요약
    print("=" * 50)
    print("결과 요약:")
    print(f"  토큰 발급: {'✅' if token else '❌'}")
    print(f"  잔고 조회: {'✅' if balance_ok else '❌'}")
    print(f"  현재가 조회: {'✅' if current_price > 0 else '❌'}")
    print(f"  주문 테스트: {'✅' if odno else '❌'}")
    print("=" * 50)


if __name__ == "__main__":
    main()
