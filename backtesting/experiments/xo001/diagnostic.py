"""Conditional one-share PAPER diagnostics, with durable intent-before-send records."""
from dataclasses import dataclass
import math

from . import Refused, aware
from .activation import diagnostic_enabled
from .calendar import time_map
from .kis import CANCEL, INQUIRY, ORDER, NotTransmitted
from .storage import encoded, sha


@dataclass
class OrderPermit:
    tr_id: str
    body_sha256: str
    start: object
    end: object
    used: bool = False

    def consume(self, tr_id, body, at):
        valid = (not self.used and tr_id == self.tr_id and sha(encoded(body)) == self.body_sha256
                 and aware(self.start) <= aware(at) < aware(self.end))
        self.used = True
        return valid


def _integer(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise Refused("invalid PAPER order inquiry quantity") from None
    if str(number) != str(value) or number < 0:
        raise Refused("invalid PAPER order inquiry quantity")
    return number


class Diagnostic:
    def __init__(self, store, client, current_binding, *, gate=diagnostic_enabled):
        self.store, self.client, self.binding, self.gate = store, client, current_binding, gate

    def enabled(self):
        return self.gate(self.store, self.binding)

    def seal_intents(self, plan, decisions):
        if not self.enabled():
            return {"status": "OFF"}
        times, day = time_map(plan), plan["date"]
        if plan["special"]:
            return {"status": "not_submitted", "reason": "normal_days_only"}
        old = self.store.get("order_intents", day)
        if old:
            return old
        if aware(self.store.clock()) >= times["s0859"]:
            raise Refused("order intentions must be sealed by 08:59")
        targets = [{"rcept_no": d["event"]["rcept_no"], "stock_code": d["event"]["stock_code"],
                    "quantity": 1, "order_type": "01", "side": "buy",
                    "decision_sha256": d["xo001_input_sha256"]}
                   for d in sorted(decisions, key=lambda d: d["event"]["rcept_no"])]
        return self.store.seal("order_intents", day, {"date": day, "targets": targets},
                               deadline=times["s0859"])["record"]

    def record_omitted_targets(self, plan, decisions):
        if not self.enabled():
            return
        intents = self.store.get("order_intents", plan["date"])
        known = {t["rcept_no"] for t in intents["targets"]} if intents else set()
        for row in decisions:
            event = row["event"]
            if event["rcept_no"] not in known:
                self.store.seal("order_skip", plan["date"] + "." + event["rcept_no"] + ".no_intent",
                    {"date": plan["date"], "rcept_no": event["rcept_no"], "stock_code": event["stock_code"],
                     "quantity": 1, "state": "not_submitted", "reason": "not_in_timely_sealed_intent_set",
                     "account_sha256": self.binding["account_sha256"]})

    def finish_submissions(self, plan, *, clock_verified):
        intents = self.store.get("order_intents", plan["date"])
        if not intents:
            return
        for index, target in enumerate(intents["targets"]):
            key = plan["date"] + f".{index:06d}.buy"
            if not self.store.proof("order_attempt", key) and not self.store.proof("order_skip", key):
                self.store.seal("order_skip", key, {**target, "date": plan["date"], "state": "not_submitted",
                    "reason": "submission_window_missed" if clock_verified else "clock_unverified",
                    "account_sha256": self.binding["account_sha256"]})

    def account(self, plan, evidence):
        """Coordinator-supplied account screen/export, no unallowlisted balance TR.

        The exact API allowlist has no balance endpoint. Missing account proof
        is an explicit no-order state; daily inquiry never substitutes for it.
        """
        times, day = time_map(plan), plan["date"]
        required = {"date", "as_of", "account_sha256", "positions", "open_orders", "cash", "buying_power",
                    "margin_per_share", "settlement_basis", "source_sha256", "coordinator_verified"}
        if (set(evidence) != required or evidence["date"] != day
                or evidence["account_sha256"] != self.binding["account_sha256"]
                or evidence["coordinator_verified"] is not True
                or not times["pre_start"] <= aware(evidence["as_of"]) < times["s0859"]
                or aware(evidence["as_of"]) > aware(self.store.clock())):
            raise Refused("missing/stale/unverified main-period account evidence")
        for key in ("cash", "buying_power"):
            if type(evidence[key]) not in (int, float) or not math.isfinite(evidence[key]) or evidence[key] < 0:
                raise Refused("invalid account cash/buying power evidence")
        if any(type(q) is not int or q < 0 for q in evidence["positions"].values()):
            raise Refused("invalid account positions")
        if (not evidence["settlement_basis"] or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
                                                   for v in evidence["margin_per_share"].values())):
            raise Refused("unverified margin/settlement basis")
        self.store.bytes(evidence["source_sha256"])
        return self.store.seal("account", day, evidence, deadline=times["s0859"])

    def _orders(self):
        attempts = {key: row for key in self.store.keys("order_attempt")
                    if (row := self.store.get("order_attempt", key))["account_sha256"] == self.binding["account_sha256"]}
        rows = {}
        for key in self.store.keys("order_result"):
            row = self.store.get("order_result", key)
            rows[row["attempt_key"]] = row
        latest, query_days = {}, {}
        for key in self.store.keys("order_inquiry"):
            record = self.store.get("order_inquiry", key)
            if record["account_sha256"] != self.binding["account_sha256"]:
                continue
            query_days[record["date"]] = record
            if record["complete"]:
                for row in record["orders"]:
                    latest[(record["date"], row["order_no"])] = row
        result = []
        for key, attempt in attempts.items():
            response = rows.get(key)
            order = {**attempt, "attempt_key": key, "state": "UNKNOWN", "filled": 0, "remaining": None}
            if response:
                order.update({k: response.get(k) for k in ("state", "order_no", "organization")})
                queried = latest.get((attempt["date"], response.get("order_no")))
                if queried:
                    if any(queried[k] != attempt[k] for k in ("stock_code", "side", "quantity")):
                        raise Refused("PAPER inquiry identity/quantity differs from the sealed intent")
                    order.update(queried)
                query = query_days.get(attempt["date"])
                if query and order["state"] not in {"filled", "cancelled", "rejected", "not_submitted", "late"} and (
                        not query["complete"] or response.get("order_no") not in {q["order_no"] for q in query["orders"]}):
                    order["state"] = "UNKNOWN"
            result.append(order)
        return result

    def positions(self):
        positions = {}
        for row in self._orders():
            positions.setdefault(row["stock_code"], 0)
            positions[row["stock_code"]] += row["filled"] * (1 if row["side"] == "buy" else -1)
        if any(q < 0 for q in positions.values()):
            raise Refused("PAPER diagnostic fill ledger implies a short position")
        return {c: q for c, q in positions.items() if q}

    def cleared(self):
        return not self.positions() and not any(r["state"] in {"UNKNOWN", "accepted", "open", "partial"} for r in self._orders())

    def pending_dates(self):
        return sorted({r["date"] for r in self._orders() if r["state"] in {"UNKNOWN", "accepted", "open", "partial"}})

    def _blocked(self, day, account):
        orders = self._orders()
        if any(r["state"] == "UNKNOWN" for r in orders):
            return "UNKNOWN_order_remains"
        if self.positions() or any(account["positions"].values()):
            return "unflattened_position"
        if account["open_orders"] or any(r["date"] < day and r["state"] in {"accepted", "open", "partial"} for r in orders):
            return "prior_open_order_remains"
        return None

    def _body(self, code, side, quantity, order_type="01", price=0):
        c = self.client.credentials
        return {"CANO": c.cano, "ACNT_PRDT_CD": c.product, "PDNO": code, "ORD_DVSN": order_type,
                "ORD_QTY": str(quantity), "ORD_UNPR": str(price), "EXCG_ID_DVSN_CD": "KRX"}

    def _send(self, key, value, body, tr_id, start, end):
        if self.store.proof("order_attempt", key):
            return {"status": "already_attempted", "attempt_key": key}
        # The durable attempt exists before token/rate/transport work. A crash is UNKNOWN.
        self.store.seal("order_attempt", key, {**value, "account_sha256": self.binding["account_sha256"],
            "body_sha256": sha(encoded(self.client.redactor.clean(body)))})
        permit = OrderPermit(tr_id, sha(encoded(body)), start, end)
        try:
            envelope = self.client.request("POST", CANCEL if tr_id == "VTTC0013U" else ORDER, tr_id,
                body=body, deadline=end, permit=permit)
            data = envelope.get("data") or {}
            output = data.get("output") or {}
            order_no = output.get("ODNO") or output.get("odno")
            organization = output.get("KRX_FWDG_ORD_ORGNO") or output.get("krx_fwdg_ord_orgno")
            state = "rejected" if data.get("rt_cd") not in (None, "0") else (
                "accepted" if not envelope["error"] and order_no and organization else "UNKNOWN")
        except NotTransmitted as exc:
            state = "late" if aware(self.store.clock()) >= aware(end) else "not_submitted"
            envelope, order_no, organization = {"error": "order_HTTP_not_transmitted",
                "reason": self.client.redactor.text(str(exc))}, None, None
        except Refused:
            envelope, state, order_no, organization = {"error": "request_not_confirmed"}, "UNKNOWN", None, None
        result = {"attempt_key": key, "state": state, "order_no": order_no, "organization": organization,
                  "envelope": envelope, "acceptance_is_fill": False, "date": value["date"],
                  "account_sha256": self.binding["account_sha256"]}
        self.store.seal("order_result", key, result)
        return result

    def submit(self, plan):
        if not self.enabled():
            return {"status": "OFF"}
        times, day = time_map(plan), plan["date"]
        intents, account = self.store.get("order_intents", day), self.store.get("account", day)
        if not intents:
            return {"status": "not_submitted", "reason": "no_timely_intents"}
        at = aware(self.store.clock())
        if at < times["submit"]:
            return {"status": "waiting"}
        results, reserved = [], 0.
        for order in self._orders():
            if order["date"] == day and order["side"] == "buy" and order["state"] not in {"not_submitted", "late", "rejected"}:
                reserved += (account or {}).get("margin_per_share", {}).get(order["stock_code"], 0.)
        for index, target in enumerate(intents["targets"]):
            key = day + f".{index:06d}.buy"
            if self.store.get("order_skip", key) or self.store.proof("order_attempt", key):
                continue
            reason = "main_account_evidence_missing" if account is None else self._blocked(day, account)
            if account and account["account_sha256"] != self.binding["account_sha256"]:
                reason = "account_identity_mismatch"
            activation = self.store.get("activation", "activation")
            if activation and day > activation["account_policy"]["main_valid_through"]:
                reason = "main_account_expired_no_undefined_replacement"
            if plan["special"]:
                reason = "normal_days_only"
            if aware(self.store.clock()) >= times["publish"]:
                reason = "late"
            margin = account["margin_per_share"].get(target["stock_code"]) if account else None
            if reason is None and margin is None:
                reason = "unverified_margin_requirement"
            if reason is None and (account["cash"] < reserved + margin or account["buying_power"] < reserved + margin):
                reason = "insufficient_cash_or_buying_power"
            if reason:
                self.store.seal("order_skip", key, {**target, "date": day, "state": "late" if reason == "late" else "not_submitted",
                                                   "reason": reason, "account_sha256": self.binding["account_sha256"]})
                results.append({"state": "late" if reason == "late" else "not_submitted", "reason": reason})
                continue
            body = self._body(target["stock_code"], "buy", 1, target["order_type"], target.get("limit_price", 0))
            result = self._send(key, {**target, "date": day}, body, "VTTC0012U", times["submit"], times["publish"])
            results.append(result)
            if result.get("state") in {"accepted", "UNKNOWN"}:
                reserved += margin
        return {"status": "recorded", "results": results}

    def inquire(self, day, *, deadline):
        if not self.enabled():
            return {"status": "OFF"}
        c = self.client.credentials
        params = {"CANO": c.cano, "ACNT_PRDT_CD": c.product, "INQR_STRT_DT": day.replace("-", ""),
            "INQR_END_DT": day.replace("-", ""), "SLL_BUY_DVSN_CD": "00", "INQR_DVSN": "00", "PDNO": "",
            "CCLD_DVSN": "00", "ORD_GNO_BRNO": "", "ODNO": "", "INQR_DVSN_3": "00", "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}
        pages, orders, seen = [], [], set()
        complete, continuation = False, ""
        for _ in range(100):
            try:
                result = self.client.request("GET", INQUIRY, "VTTC8001R", params=params,
                                             deadline=deadline, continuation=continuation)
            except Refused:
                pages.append({"data": None, "error": "inquiry_not_observed", "response_headers": {}})
                break
            pages.append(result)
            if result["error"] or not isinstance((result.get("data") or {}).get("output1"), list):
                break
            data = result["data"]
            try:
                for row in data["output1"]:
                    order_no = row["odno"]
                    quantity, filled, remaining = (_integer(row[k]) for k in ("ord_qty", "tot_ccld_qty", "rmn_qty"))
                    if (not order_no or order_no in seen or quantity <= 0 or filled + remaining > quantity
                            or row["sll_buy_dvsn_cd"] not in {"01", "02"}):
                        raise Refused("invalid/duplicate daily inquiry order")
                    seen.add(order_no)
                    cancelled = row.get("cncl_yn") == "Y"
                    fill_price = float(row["avg_prvs"]) if filled else None
                    if filled and (not math.isfinite(fill_price) or fill_price <= 0):
                        raise Refused("invalid PAPER fill price")
                    orders.append({"order_no": order_no, "stock_code": row["pdno"],
                        "side": "buy" if row["sll_buy_dvsn_cd"] == "02" else "sell",
                        "quantity": quantity, "filled": filled, "remaining": remaining,
                        "fill_price": fill_price,
                        "state": "cancelled" if cancelled and remaining == 0 else "filled" if filled == quantity else "partial" if filled else "open"})
            except (KeyError, ValueError, TypeError):
                break
            more = result["response_headers"].get("tr_cont")
            if more in {"D", "E", ""}:
                complete = True
                break
            if more not in {"F", "M"} or not data.get("ctx_area_fk100") or not data.get("ctx_area_nk100"):
                break
            params.update(CTX_AREA_FK100=data["ctx_area_fk100"], CTX_AREA_NK100=data["ctx_area_nk100"])
            continuation = "N"
        record = {"date": day, "complete": complete, "orders": orders, "pages": pages, "account_sha256": self.binding["account_sha256"],
                  "page_sizes": [len(output) if isinstance(output, list) else None
                                 for p in pages for output in [(p.get("data") or {}).get("output1")]]}
        self.store.append("order_inquiry", record)
        return record

    def cancel(self, plan):
        if not self.enabled():
            return {"status": "OFF"}
        times, results = time_map(plan), []
        if aware(self.store.clock()) < times["cancel"]:
            return {"status": "waiting"}
        for row in self._orders():
            if row["state"] not in {"open", "partial", "accepted"} or not row.get("remaining"):
                continue
            key = plan["date"] + "." + row["attempt_key"] + ".cancel"
            c = self.client.credentials
            body = {"CANO": c.cano, "ACNT_PRDT_CD": c.product,
                "KRX_FWDG_ORD_ORGNO": row["organization"], "ORGN_ODNO": row["order_no"],
                "ORD_DVSN": "00", "RVSE_CNCL_DVSN_CD": "02", "ORD_QTY": str(row["remaining"]),
                "ORD_UNPR": "0", "QTY_ALL_ORD_YN": "Y", "EXCG_ID_DVSN_CD": "KRX"}
            # Cancellation intents have their own namespace and never change holdings.
            if not self.store.proof("cancel_attempt", key):
                self.store.seal("cancel_attempt", key, {"order_no": row["order_no"], "remaining": row["remaining"]})
                permit = OrderPermit("VTTC0013U", sha(encoded(body)), times["cancel"], times["close"])
                try:
                    result = self.client.request("POST", CANCEL, "VTTC0013U", body=body, deadline=times["close"], permit=permit)
                except NotTransmitted:
                    result = {"error": "cancel_not_transmitted", "state": "not_submitted"}
                except Refused:
                    result = {"error": "cancel_outcome_unknown", "state": "UNKNOWN"}
                self.store.seal("cancel_result", key, {"envelope": result, "confirmed_cancelled": False})
                results.append(result)
        return {"status": "attempted", "results": results, "confirmation": "requires final inquiry state and remaining=0"}

    def flatten(self, plan):
        if not self.enabled():
            return {"status": "OFF"}
        times, results = time_map(plan), []
        if aware(self.store.clock()) < times["flatten"]:
            return {"status": "waiting"}
        for code, quantity in sorted(self.positions().items()):
            if any(r["stock_code"] == code and r["side"] == "sell" and r["state"] in {"UNKNOWN", "accepted", "open", "partial"}
                   for r in self._orders()):
                self.store.seal("flatten_skip", plan["date"] + "." + code,
                                {"stock_code": code, "reason": "unresolved_sell_order", "unflattened": True})
                continue
            key = plan["date"] + ".flatten." + code
            body = self._body(code, "sell", quantity)
            results.append(self._send(key, {"date": plan["date"], "stock_code": code, "side": "sell",
                "quantity": quantity, "order_type": "01"}, body, "VTTC0011U", times["flatten"], times["close"]))
        return {"status": "attempted", "results": results, "flattened": self.cleared()}

    def seal_test_intents(self, plan, *, main_account=False):
        """Only called by the explicitly enabled, coordinator-run November test CLI."""
        if not self.enabled():
            return {"status": "OFF"}
        registered = self.store.get("shakedown_plan", "november")
        rows = [registered["main_account"]] if main_account else registered["tests"]
        row = next((r for r in rows if r["date"] == plan["date"]), None)
        if row is None or plan["special"]:
            raise Refused("this is not a predetermined normal-day activation test")
        targets = [{"rcept_no": "activation_test_market", "stock_code": row["stock_code"], "quantity": 1,
                    "order_type": "01", "side": "buy"},
                   {"rcept_no": "activation_test_limit", "stock_code": row["stock_code"], "quantity": 1,
                    "order_type": "00", "limit_price": row["limit_price"], "side": "buy"}]
        return self.store.seal("order_intents", plan["date"], {"date": plan["date"], "targets": targets,
            "label": "shakedown", "main_account_test": main_account}, deadline=time_map(plan)["s0859"])["record"]
