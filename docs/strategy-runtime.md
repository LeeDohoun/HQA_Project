# 전략 실행 경로 운영 안내

연구를 통과한 전략을 전략 전용 PAPER 계좌에서 사전 등록 규칙 그대로 매매하는 경로입니다. 명세는 [실행 계획 M8](strategy-execution-plan.md#m8-전략-실행-경로-섀도paper-일괄-리밸런싱)에 있습니다. 기존 LLM 소수 종목 경로([Luna PAPER runtime](luna-paper-runtime.md))는 그대로이며, 전략 계좌에는 LLM 계획이 들어가지 않습니다.

## 구성

```text
FW001 결정 기록 (읽기 전용)
  data/forward/fw001/decisions/YYYYMM.json
        │
        ▼
src/live  ── gates: 실험 대장·섀도 장부·승인 → 실제 단계 (observe / shadow / paper)
          ── rebalance: 목표 비중 + 장부 → 주문 목록 (매도 먼저)
          ── shadow: KRX 일봉 시가·종가로 가상 체결 → data/live/<strategy_id>/shadow/
          ── backend_client: 상태 게시, (paper 단계만) 일괄 주문
        │
        ▼
백엔드  ── 전략 계좌 (LLM 대상 제외, LLM 계획 거부)
        ── 위험 한도·중단 스위치 (전략 코드와 독립)
        ── 일괄 주문 worker: 동시호가 시간 창에만, 매도 먼저, KIS 주문 내역으로 체결 대조
        │
        ▼
화면 /strategies ── 단계와 막힌 이유, 최신 결정, 섀도 장부, 일괄 주문, 위험 한도
```

## 내부 API 계약

모든 내부 API는 `X-HQA-Internal-Token` 헤더가 필요합니다. 값은 백엔드와 같은 `HQA_INTERNAL_TOKEN` 환경 변수이며, 코드가 `.env` 파일을 직접 읽지 않습니다.

### 일괄 주문 접수 `POST /api/v1/internal/strategies/batches`

```json
{
  "strategyId": "hf001_f_both",
  "userId": "user-id",
  "decisionHash": "64자리 16진수",
  "decisionDate": "2026-10-30",
  "sessionDate": "2026-11-02",
  "phase": "OPEN",
  "idempotencyKey": "64자리 16진수",
  "dryRun": true,
  "expectedNavKrw": 100000000,
  "orders": [
    {"seq": 1, "stockCode": "005930", "side": "SELL", "quantity": 10, "refPrice": 70000, "adv20Krw": 12000000000.0},
    {"seq": 2, "stockCode": "0126Z0", "side": "BUY", "quantity": 3, "refPrice": 51000, "adv20Krw": 900000000.0}
  ]
}
```

- `phase`: `OPEN`(장전 동시호가, 시가 체결) 또는 `CLOSE`(장 마감 동시호가, 종가 체결).
- `idempotencyKey` = sha256(`strategyId|decisionHash|sessionDate|phase|userId`)의 16진수.
- 매도 주문의 `seq`가 모든 매수 주문보다 작아야 합니다. (`stockCode`, `side`)는 중복될 수 없습니다.
- 응답:

```json
{"batchId": "uuid 또는 null", "status": "VALIDATED | QUEUED | REJECTED", "deduplicated": false,
 "reasons": ["..."], "orderCount": 2, "rejectedOrders": [{"seq": 2, "reason": "ORDER_EXCEEDS_EQUITY_SHARE"}]}
```

- `dryRun: true`는 검증만 하고 저장하지 않습니다 (`VALIDATED` 또는 `REJECTED`). 시간 창 검사는 건너뜁니다.
- 거부 사유: `INVALID_REQUEST`, `NO_ORDERS`, `IDEMPOTENCY_KEY_MISMATCH`, `STRATEGY_ACCOUNT_NOT_FOUND`, `STRATEGY_MISMATCH`, `STRATEGY_STAGE_NOT_PAPER`, `STRATEGY_ACCOUNT_DISABLED`, `PAPER_ACCOUNT_REQUIRED`(뒤에 세부 원인, 예 `KIS_SECRET_MISSING`), `NOT_A_KRX_SESSION`, `CALENDAR_UNVERIFIED`, `SESSION_DATE_NOT_TODAY`, `OUTSIDE_PHASE_WINDOW`, `BATCH_ALREADY_EXISTS_FOR_PHASE`, `TRADING_HALTED`, `ACCOUNT_SNAPSHOT_UNAVAILABLE`, `DAILY_BASELINE_UNAVAILABLE`, `DAILY_LOSS_LIMIT_EXCEEDED`, `DRAWDOWN_STOP`, `DAILY_ORDER_LIMIT_EXCEEDED`, `CANCEL_RATIO_EXCEEDED`, `ORDER_EXCEEDS_EQUITY_SHARE`, `SELLS_MUST_PRECEDE_BUYS`, `DUPLICATE_ORDER`, `INVALID_ORDER`, `TOO_MANY_ORDERS`.
- 서버가 `idempotencyKey`를 다시 계산해 비교합니다. 같은 키는 저장된 batch의 현재 상태를 돌려줍니다(`deduplicated: true`). 거부된 요청은 저장하지 않습니다.
- 한 계좌의 같은 세션·phase에는 거부되지 않은 batch가 하나만 있을 수 있습니다(`BATCH_ALREADY_EXISTS_FOR_PHASE`, DB 부분 unique 인덱스). 결정 기록이 바뀌어 해시가 달라져도 두 번 주문하지 않습니다.
- 매수가 막힌 상태(일일 손실, 낙폭)에서는 매수가 들어 있는 batch 전체가 거부됩니다. 매도만 담은 batch는 접수됩니다. 중단 스위치는 모두 막습니다.
- 달력: 백엔드 검토 범위(`hqa.strategy.calendar-reviewed-through`, 기본 2027-09-30) 밖이거나, 공지 대기 중인 특별 세션(`hqa.strategy.pending-special-sessions`, 기본 2026-11-19)에 시간이 설정되지 않았으면 `CALENDAR_UNVERIFIED`입니다.

### 일괄 주문 조회 `GET /api/v1/internal/strategies/batches/{batchId}`

```json
{"batchId": "...", "strategyId": "...", "userId": "...", "phase": "OPEN", "sessionDate": "2026-11-02",
 "status": "QUEUED | SUBMITTING | SUBMITTED | COMPLETED | PARTIAL | REJECTED | HALTED",
 "orders": [{"seq": 1, "stockCode": "005930", "side": "SELL", "quantity": 10, "refPrice": 70000,
             "status": "PENDING | SUBMITTED | FILLED | PARTIALLY_FILLED | REJECTED | CANCELLED | UNKNOWN | SKIPPED",
             "brokerOrderId": null, "filledQuantity": 0, "averageFillPrice": null, "rejectReason": null}]}
```

### 상태 게시 `POST /api/v1/internal/strategies/status`

```json
{"strategyId": "hf001_f_both", "generatedAt": "2026-10-30T17:10:00+09:00", "payload": { "아래 상태 형식": "" }}
```

응답 `{"stored": true}`. 같은 전략은 최신 하나만 보관합니다.

### 전략 계좌 `GET|POST /api/v1/internal/strategies/accounts`

- `POST` 본문: `{"userId": "...", "strategyId": "hf001_f_both", "stage": "observe | shadow | paper", "enabled": false}`. PAPER KIS 계좌가 연결된 사용자만 가능합니다. 한 사용자에 전략 하나, 한 전략에 사용자 하나입니다.
- `GET` 응답: `{"accounts": [{"userId": "...", "strategyId": "...", "stage": "...", "enabled": false, "updatedAt": "..."}]}`

- `DELETE /api/v1/internal/strategies/accounts/{userId}`: 진행 중인 batch나 열린 주문(PENDING, SUBMITTED, PARTIALLY_FILLED, UNKNOWN)이 있으면 409 `STRATEGY_ORDERS_OPEN`입니다.
- 연결할 때 활성 LLM 계획이나 미해결 LLM 주문이 있으면 `ACTIVE_LLM_PLANS_PRESENT`로 거부합니다. 새로 연결하면 그 계좌의 순자산 고점 기록을 초기화합니다(응답 `equityPeakReset`).
- 전략 계좌에는 LLM 계획(`STRATEGY_ACCOUNT_REJECTS_LLM_PLANS`)과 화면의 수동 매수·매도(`STRATEGY_ACCOUNT_REJECTS_MANUAL_ORDERS`)가 들어가지 않습니다. 기존 보유의 보호 매도는 영향받지 않습니다.

### 접수 불명 주문 `GET /api/v1/internal/strategies/orders/unknown`, `POST .../orders/{orderId}/resolution`

- 주문은 보내기 전에 UNKNOWN으로 먼저 저장하고, 응답이 모호하면 UNKNOWN으로 남깁니다. **다시 보내지 않습니다.**
- 운영자는 KIS 모의투자 주문 내역에서 찾아 처리합니다.
  - `{"action": "adopt", "brokerOrderId": "...", "note": "..."}`: 그 주문일의 KIS 주문 내역에 같은 종목·방향·수량이고 다른 주문에 연결되지 않은 주문이 있어야 합니다. 이후 체결 대조가 이어집니다.
  - `{"action": "not-submitted", "note": "..."}`: KIS에 가능한 주문이 보이면 거부합니다(`BROKER_LISTS_POSSIBLE_ORDER:...`). 성공하면 `SKIPPED / OPERATOR_CONFIRMED_NOT_SUBMITTED`.
- 지난 세션에 끝나지 않은 batch는 정리 단계에서 PENDING은 `SKIPPED / SESSION_PASSED`, 대조할 수 없는 주문은 `UNKNOWN / UNRESOLVED_AFTER_SESSION`이 됩니다.

### 중단 스위치 `POST /api/v1/internal/strategies/halt`

`{"scope": "GLOBAL 또는 userId", "halted": true, "reason": "..."}`. 켜져 있으면 새 일괄 주문 접수와 미전송 주문 전송이 모두 멈춥니다.

### 위험 상태 `GET /api/v1/internal/strategies/risk/{userId}`

```json
{"userId": "...", "equityKrw": 100000000, "dailyPnlPct": -0.4, "peakEquityKrw": 101000000, "drawdownPct": 0.99,
 "buyAllowed": true, "fullReviewRequired": false, "halted": false, "reasons": [], "ordersToday": 0, "cancelsToday": 0,
 "limits": {"dailyLossLimitPct": 2.0, "drawdownStopPct": 10.0, "fullReviewPct": 15.0, "maxOrdersPerDay": 1500,
            "maxCancelRatio": 0.5, "maxOrderPctOfEquity": 10.0}}
```

### 화면용 `GET /api/v1/strategies` (로그인 사용자)

```json
{"strategies": [{"strategyId": "...", "updatedAt": "...", "payload": {"상태 형식": ""},
                 "latestBatch": {"batchId": "...", "phase": "OPEN", "sessionDate": "...", "status": "...",
                                 "orderCount": 0, "filledCount": 0, "rejectedCount": 0, "avgSlippagePct": null},
                 "risk": null}],
 "halts": [{"scope": "GLOBAL", "halted": false, "reason": null, "updatedAt": "..."}]}
```

`avgSlippagePct`는 체결된 주문의 (평균 체결가 − 기준가) / 기준가 × 100의 평균이며, 매도는 부호를 바꿉니다 (불리한 쪽이 양수).

## FW001 결정 기록의 봉인 표식

FW001 기록기는 확정한 기록마다 봉인 표식을 따로 씁니다. 실행 경로는 표식이 유효한 기록만 확정 결정으로 읽습니다 (필드 목록은 FW001 커밋에서 최종 확인 예정, `src/live/decisions.py`의 `parse_seal_marker` 한 곳에서 처리).

- 결정: `data/forward/fw001/sealed/<YYYYMM>.decision.json`, 체결: `.../sealed/<YYYYMM>.execution.<배수>.json`
- 필드: `record_path`, `record_digest`(기록 파일 바이트의 sha256), `sealed_at`, `deadline`, `reconstructed`(false)
- 확인: 표식이 가리키는 파일이 맞고, 바이트 해시가 같고, 재구성 기록이 아니고, 마감 전에 봉인됐고, 기록 `label`이 `forward`.
- 실패하면 결정을 쓰지 않고 이유를 `stage.reasons`에 `<이유>:<YYYY-MM>`로 남깁니다: `decision_not_sealed`, `decision_seal_invalid`, `decision_seal_path_mismatch`, `decision_seal_digest_mismatch`, `decision_seal_reconstructed`, `decision_seal_after_deadline`, `decision_hash_marker_mismatch`. 진입일이 지나도 봉인된 결정이 없으면 섀도 장부의 운영 사고로 셉니다.
- `decisionHash`는 FW001 `digest()`와 같은 인코딩이라 FW001 체결 기록의 `decision_hash`와 직접 비교됩니다.

## 상태 형식 (Python이 만들고 화면이 읽음)

```json
{
  "strategyId": "hf001_f_both",
  "title": "HF001 f_both",
  "description": "HC002 2국면 동일가중에서 고변동성·최근 유상증자 종목 제외, 월말 결정, 다음 날 시가 진입, 20거래일 보유",
  "generatedAt": "2026-10-30T17:10:00+09:00",
  "stage": {"requested": "shadow", "effective": "observe", "reasons": ["holdout_not_passed:HF001_hegemony_filters/holdout_f_both:none"]},
  "research": {
    "experimentId": "HF001_hegemony_filters", "variant": "f_both",
    "design": {"verdict": "pass", "recordedAt": "2026-10-17T12:11:15+00:00"},
    "holdout": {"experimentId": null, "variant": null, "verdict": null, "recordedAt": null}
  },
  "decision": {"month": "2026-10", "decisionDate": "2026-10-30", "tradeDate": "2026-11-02", "exitDate": "2026-11-27",
               "targetCount": 586, "recordHash": "...", "label": "forward"},
  "shadow": {"mode": "observe", "sessions": 0, "firstDate": null, "lastDate": null, "initialNavKrw": 100000000,
             "navKrw": 100000000, "returnPct": 0.0, "incidents": 0, "history": [{"date": "2026-11-02", "navKrw": 100000000}]},
  "plan": {"sessionDate": "2026-11-02", "phase": "OPEN", "buyCount": 586, "sellCount": 0,
           "buyNotionalKrw": 99500000, "sellNotionalKrw": 0, "advWarnings": 0},
  "account": {"userId": null, "bound": false}
}
```

`decision`, `plan`은 없으면 `null`입니다. `history`는 최근 120개까지입니다.

## 명령

모든 명령은 기본이 읽기 전용(dry-run)이고, 파일 쓰기와 네트워크 전송은 `--execute`가 있을 때만 합니다.

```bash
venv/bin/python -m scripts.ops.strategy_runtime status
venv/bin/python -m scripts.ops.strategy_runtime plan --strategy hf001_f_both --session 2026-11-02
venv/bin/python -m scripts.ops.strategy_runtime shadow-fill --session 2026-11-02 --execute
venv/bin/python -m scripts.ops.strategy_runtime publish-status --execute
venv/bin/python -m scripts.ops.strategy_runtime submit --strategy hf001_f_both --session 2026-11-02 --phase OPEN --execute
```

- `plan`/`submit` 출력의 `funding`: `deposit_cash_krw`, `sell_proceeds_assumed_krw`, `buy_required_krw`, `requires_same_auction_sell_proceeds`. `--max-buy-to-deposit`을 주면 매수액이 예수금을 넘을 때 제출하지 않습니다 (수량을 줄이지 않음).
- `shadow-fill --execute`는 그 세션 16:30 KST 이후에만 기록하고, KRX 일봉 파일이 없으면 기록하지 않고 사고 파일만 남깁니다. 대상·보유 종목 행이 빠지면 `price_row_missing:<종목>` 사고입니다.

## PAPER 승격 조건 (코드가 확인)

`observe → shadow`: 실험 대장의 설계·검증 `pass`와 지정한 보류 판정 행의 `pass`.
`shadow → paper`: 위 조건 + 섀도 단계 연속 20거래일·28일 이상 + 운영 사고 0건 + 섀도 체결이 FW001 체결 기록과 독립 대조로 일치(`shadow_independent_check_failed`/`_unavailable`이 없어야 함) + `config/strategy_approvals.yaml`의 사람 승인.
2026-10-18 현재 두 전략 모두 `observe`입니다 (HF001 보류 구간 미평가, AV001 보류 구간 `holdout_aborted`, AV002 재평가 예정).

## 처음 가동 순서 (모두 사람 확인 필요)

1. FW001 기록기가 커밋·배포되고 봉인 표식 필드가 확정됩니다 (FW001 담당 세션).
2. 서버에서 `status`(읽기 전용) → 결정일 이후 `plan` → 진입일 장 마감 뒤 `shadow-fill --execute`를 하루 한 번. 일정 등록은 사용자 승인 후.
3. `publish-status --execute`로 화면에 게시 (백엔드 V15 migration 적용 후).
4. 보류 구간 통과 후 `config/strategy_book.yaml`의 요청 단계가 `shadow`인 전략은 자동으로 섀도 단계가 됩니다.
5. PAPER 전: 전략 전용 KIS 모의투자 계좌를 가진 사용자를 만들고 `POST /api/v1/internal/strategies/accounts`로 연결(`enabled: false`), 그 사용자의 LLM 자동매매를 끕니다.
6. **KIS 확인 호출 (사용자 승인 필요, 소량 1~2주)**: 장전 동시호가(08:30~08:59)·장 마감 동시호가(15:20~15:29)에 모의투자 시장가 주문(ORD_DVSN 01) 접수 여부와 ODNO, 체결 가격이 시가·종가인지, 주문 내역(VTTC0081R) 필드, 시장가 잔량 취소(VTTC0013U), 시장가 매수 증거금 기준(상한가 기준인지), 같은 동시호가의 매도 대금이 매수 가능 금액에 잡히는지, 일별 주문 조회 페이지 크기.
7. 승인 기록을 `config/strategy_approvals.yaml`에 남기고 `HQA_STRATEGY_ORDERS_ENABLED=true`, 계좌 `enabled: true, stage: paper`.

## 아직 확인하지 못한 것

- 위 6의 KIS 동작 전부. 확인 전에는 `orders-enabled`를 켜지 않습니다.
- V15 migration과 엔티티 매핑을 실제 Postgres에서 적용해 보지 않았습니다 (`HQA_TEST_DATABASE_URL` 테스트 미실행).
- 처리량: 1 req/s 제한에서 586건은 약 10분입니다. 장전 창 30분 안에 들어가지만 같은 앱 키의 다른 호출과 나눠 씁니다.
- 연속 리밸런싱(AV001)은 매도 대금으로 매수를 채우는 HC003 규칙입니다. 장전에는 매도 대금이 없어 매수 일부가 거부될 수 있습니다. 확인 결과에 따라 연구 쪽 판단이 필요합니다.
- 2027년 1월 이후 세션은 공식 휴장일 공지 반영 전까지 연구 달력에서 막힙니다 (`research/experiments/OPS_TODO_2027_KRX_CALENDAR.md`, 기한 2026-11-27).
