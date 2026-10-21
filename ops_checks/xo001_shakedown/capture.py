"""XO001 quote-only shakedown capture (not the formal recorder; not part of the main sample).

PAPER domain only. Polls the expected-execution/order-book endpoint for a fixed list
round-robin at <= 1 request/second from 08:24:30 to 09:00:00 KST, then the current-price
endpoint until 09:06. Writes one JSON line per response with UTC, monotonic and provider
times. Never prints or stores credentials or tokens.
"""
import datetime as dt, json, os, sys, time
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

BASE = "https://openapivts.koreainvestment.com:29443"
KST = ZoneInfo("Asia/Seoul")
CODES = ["005930", "000660", "035420", "247540", "196170", "086520"]

def now():
    return dt.datetime.now(KST)

def main(out_dir):
    key, secret = os.environ["XO001_KIS_PAPER_APP_KEY"], os.environ["XO001_KIS_PAPER_APP_SECRET"]
    day = now().strftime("%Y%m%d")
    out = Path(out_dir) / f"shakedown_{day}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    r = session.post(BASE + "/oauth2/tokenP", json={"grant_type": "client_credentials", "appkey": key, "appsecret": secret}, timeout=15)
    token = r.json().get("access_token")
    if not token:
        print(json.dumps({"event": "token_failed", "http": r.status_code})); return 1
    def request(path, tr, code):
        headers = {"authorization": "Bearer " + token, "appkey": key, "appsecret": secret, "tr_id": tr, "custtype": "P"}
        sent_utc, mono = dt.datetime.now(dt.timezone.utc).isoformat(), time.monotonic_ns()
        try:
            resp = session.get(BASE + path, headers=headers, params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}, timeout=10)
            body, http = resp.json(), resp.status_code
        except Exception as exc:
            body, http = {"error": type(exc).__name__}, None
        return {"code": code, "tr_id": tr, "sent_utc": sent_utc, "received_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                "mono_ns": mono, "latency_ms": (time.monotonic_ns() - mono) / 1e6, "http": http, "body": body}
    with out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": "start", "at": now().isoformat(), "codes": CODES}) + "\n")
        i = 0
        while now().time() < dt.time(9, 6):
            phase_pre = now().time() < dt.time(9, 0)
            tr, path = (("FHKST01010200", "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn") if phase_pre
                        else ("FHKST01010100", "/uapi/domestic-stock/v1/quotations/inquire-price"))
            t0 = time.monotonic()
            rec = request(path, tr, CODES[i % len(CODES)])
            handle.write(json.dumps(rec, ensure_ascii=False) + "\n"); handle.flush()
            i += 1
            time.sleep(max(0.0, 1.05 - (time.monotonic() - t0)))
        handle.write(json.dumps({"event": "end", "at": now().isoformat(), "requests": i}) + "\n")
        handle.flush(); os.fsync(handle.fileno())
    print(json.dumps({"event": "done", "requests": i, "file": str(out)}))
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/var/lib/hqa-xo001-shakedown"))
