from scripts.data.top_value import FIELDS, MASTERS, parse_master, select_targets


def _line(market, code, name, group="ST", price=10_000, volume=1_000, halted="N", liquidating="N",
          administrative="N", warning="00"):
    tail = [" "] * MASTERS[market][1]
    values = {"group": group, "price": str(price).zfill(9), "halted": halted, "liquidating": liquidating,
              "administrative": administrative, "warning": warning, "volume": str(volume).zfill(12)}
    for field, (start, end) in FIELDS[market].items():
        tail[start:end] = values[field]
    # code (9) + standard code (12) + name, then the fixed-width tail ending with the newline
    return code.ljust(9) + "KR7" + code + "000" + name + "".join(tail[:-1])


def test_ranks_common_stocks_by_trading_value_and_keeps_holdings():
    kospi = "\n".join([
        _line("KOSPI", "000001", "큰거래", price=50_000, volume=1_000_000),
        _line("KOSPI", "000002", "중간", price=10_000, volume=1_000_000),
        _line("KOSPI", "000003", "작은보유", price=1_000, volume=10),
        _line("KOSPI", "000004", "ETF", group="EF", price=90_000, volume=9_000_000),
        _line("KOSPI", "000005", "정지", halted="Y", price=90_000, volume=9_000_000),
        _line("KOSPI", "000006", "관리", administrative="Y", price=90_000, volume=9_000_000),
        _line("KOSPI", "000007", "경고", warning="02", price=90_000, volume=9_000_000),
        _line("KOSPI", "000008", "제1호스팩", price=90_000, volume=9_000_000),
        _line("KOSPI", "000009", "코드없음", price=90_000, volume=9_000_000),
    ]) + "\n"
    kosdaq = _line("KOSDAQ", "100001", "코스닥주의", warning="01", price=20_000, volume=1_000_000) + "\n"
    rows = parse_master(kospi, "KOSPI") + parse_master(kosdaq, "KOSDAQ")
    assert rows[0]["trading_value"] == 50_000 * 1_000_000 and rows[-1]["market"] == "KOSDAQ"

    corp = {code: "C" + code for code in ("000001", "000002", "000003", "000004", "000005",
                                          "000006", "000007", "000008", "100001")}
    chosen = select_targets(rows, corp, top=2, held={"000003"})
    # Investment caution (01) is allowed; ETFs, halted, administrative, warning, SPAC and
    # stocks without a DART corp code are not; a holding outside the top N is kept.
    assert [row["stock_code"] for row in chosen] == ["000001", "100001", "000003"]
    assert chosen[0]["corp_code"] == "C000001"
