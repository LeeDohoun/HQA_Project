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

## FW001 결정 기록의 완료 증명

결정이 확정됐는지는 FW001 코드의 `src.forward.fw001.completion_proof()`로 판정합니다 (같은 코드 원칙; 계약 원문은 `research/experiments/FW001_forward_replication/implementation_notes.md`). 실행 경로는 증명 디렉터리를 읽기만 합니다.

- 증명 위치: `data/forward/fw001/sealed/<YYYYMM>.decision/complete.json`, `.../sealed/<YYYYMM>.execution.<배수>/complete.json`. 증명마다 전용 디렉터리가 있고, 봉인 시각은 그 디렉터리의 커널 mtime입니다. 1초 정밀도 상한까지 포함한 공개 구간 전체가 진입일 08:59 KST 전이어야 합니다.
- 판정 (`src/live/decisions.py`의 `completion()`):
  - 증명이 유효하면 확정 결정입니다.
  - 기록 파일이 없으면 `decision_missing`, 증명이 없으면 `decision_not_sealed`.
  - 늦은 봉인이면 `decision_seal_late`: 그달은 거래하지 않습니다 (FW001 6절의 누락 처리). 다음 달은 계속합니다.
  - 손상·불일치 증거는 `decision_seal_invalid`이며, "없음"이 아니라 오류로 상태의 `errors`에 표시합니다.
  - `decisionHash`는 `fw001.digest(record)`이며, 증명의 `record_digest`와 다르면 `decision_hash_marker_mismatch`.
- 상태의 `stage.reasons`에는 `<이유>:<YYYY-MM>`가 붙고, 진입일에 확정 결정이 없으면 섀도 장부의 운영 사고가 됩니다.
- 섀도 독립 대조는 `executions` 증명(배수 1.0)이 유효할 때만 합니다. 손상된 체결 증거는 `execution_seal_invalid` 사고, 없거나 늦으면 `unavailable`(PAPER 승격 근거가 되지 않음).

## 배포 구성

- 수집 서버와 FW001 최종 평가는 FW001 봉인본(`release/collector-fw001`, FW001 담당 세션이 관리)에서만 실행합니다. FW001은 실행 트리 전체(`src/`, `scripts/ops` 등)가 봉인 목록과 같아야 실행되므로, M8이나 팀 브랜치가 들어간 트리는 수집 서버에 배포하지 않습니다.
- M8 실행기는 별도 체크아웃(이 브랜치)에서 실행하고, FW001 자료는 파일·디렉터리 mtime을 보존해 받습니다 (`rsync -a`). mtime이 바뀐 사본은 유효한 완료 증명이 아닙니다.
- 이 브랜치에서 `tests/test_forward_fw001.py`(30건), `tests/test_experiments_fw001.py`(12건), `tests/test_ops_fw001.py`(3건)는 실패합니다. FW001이 봉인되지 않은 트리를 거부하기 때문이며, 예상된 실패입니다. FW001 검증은 봉인본 트리에서 합니다.

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
**조기 PAPER (사용자 결정 예외)**: `config/strategy_book.yaml`의 전략 항목에 `early_paper: {decided_by, decided_at, reason}`이 있고 `requested_stage: paper`이면, 위 조건 없이 실제 단계가 `paper`가 됩니다. 충족하지 않은 조건은 `stage.reasons`에 `early_paper_override`와 `gate_bypassed:<원래 이유>`로 계속 남고, 화면에 "조기 PAPER" 안내가 뜹니다. 이 기간 성과는 실계좌 판단 근거로 바로 쓰지 않습니다. 모의투자 단계에만 해당하며, 주문 전송(`orders-enabled`)·KIS 확인·계좌 연결 조건은 그대로입니다.

2026-10-21 사용자 결정으로 두 전략(HF001 f_both, AV001 av_all) 모두 조기 PAPER입니다. 보류 구간은 HF001 미평가, AV001 `holdout_aborted`(AV002 재평가 예정)입니다.

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

## 하루 운영 일정과 알림

| 시각 (평일 KST) | 작업 | 내용 |
| --- | --- | --- |
| 09:10 | `deploy/strategy-runtime/morning.sh` → `strategy_runtime daily --when morning --execute` | 수집 자료 복사 → 지난 세션 섀도 장부 작성(오래된 순) → 오늘이 진입일이면 FW001 완료 증명 확인(08:59 마감) → 오늘 할 주문 계획 → 상태 → 상태 게시 → 백엔드 확인 → 알림(일일 요약 포함) |
| 16:30 | `deploy/strategy-runtime/evening.sh` → `strategy_runtime daily --when evening --execute` | 수집 자료 복사 → 백엔드 확인(그날 일괄 주문·종가 동시호가 결과, 접수 불명 주문, 위험) → 아침에 일봉이 없던 세션 재시도 → 알림 |

- KRX는 D일 일봉을 **다음 영업일 08:00**에 공개합니다 (수집기 08:02~08:40 수집). 그래서 D일 섀도 장부는 D+1일 아침에 씁니다. 월요일 아침에는 금요일을 씁니다. FW001 월말 결정도 진입일 아침 08:00~08:59에 기록·봉인됩니다.
- 아침에 직전 세션 일봉이 아직 없으면 `price_file_pending`으로 기다리고 사고로 세지 않습니다. 하루가 더 지나도 없으면 `price_file_missing` 사고이며, 그 전략의 장부는 그 세션에서 멈춥니다 (빈 칸 뒤에 새 세션을 쓰지 않음).
- 첫 실행에서 장부가 없는 전략은 직전 세션부터 시작합니다.
알림 (`src/live/alerts.py`):

- 긴급: `status_error`, `shadow_incident`, `incident_file`, `decision_not_final`, `daily_step_failed`, `systemd_unit_failed`, `unknown_orders`, `batch_problem`, `risk_buy_blocked`, `risk_full_review`, `trading_halted`
- 주의: `calendar_review`, `funding_requires_sell_proceeds`, `adv_warnings`, `stage_blocked`(단계가 바뀔 때만)
- 정보: `daily_summary`(매일 아침 전략별 단계·순자산·사고·다음 할 일)
- 중복 억제: 긴급은 같은 내용을 하루 한 번, 주의는 7일에 한 번 (`data/live/alerts/state.json`). 전송에 실패하면 다음 실행에서 다시 보냅니다.
- 채널: `HQA_ALERT_CHANNELS=file,telegram`. `file`은 항상 `data/live/alerts/YYYYMMDD.jsonl`에 남깁니다.

### 텔레그램 설정

1. 텔레그램에서 @BotFather에 `/newbot` → 봇 토큰을 받습니다.
2. 만든 봇에 메시지를 한 번 보냅니다. 브라우저에서 `https://api.telegram.org/bot<토큰>/getUpdates`를 열어 `"chat":{"id": ...}` 값을 확인합니다.
3. 실행 서버의 `/etc/hqa/strategy-runtime.env`(권한 600)에 `HQA_TELEGRAM_BOT_TOKEN`, `HQA_TELEGRAM_CHAT_ID`, `HQA_ALERT_CHANNELS=file,telegram`을 넣습니다. 토큰은 저장소·로그·실행 요약에 남기지 않습니다.
4. 수동 시험: `venv/bin/python -m scripts.ops.strategy_runtime alerts --execute` (실제 메시지가 나갑니다).

### 설치 (사람 확인 필요) — 연구실 hqa-server

실행 서버는 연구실 `hqa-server`(Tailscale, `ssh hqa-lab`)입니다. 수집기와 FW001도 2026-10-21부터 이 서버에서 돌며, 그 코드(`/opt/hqa`)는 FW001 봉인본이라 건드리지 않습니다.

1. 이 브랜치를 `/opt/hqa-runtime`에 받고 venv를 만듭니다 (`/opt/hqa`가 아님). 자료 폴더는 `/opt/hqa-runtime/data`, `HQA_DATA_DIR=/opt/hqa-runtime/data`입니다.
2. 같은 서버의 수집 자료는 `HQA_COLLECTOR_LOCAL_DIR=/var/lib/hqa/data`로 지정합니다. 실행 전에 `rsync -a`로 필요한 폴더(disclosures, market, ops, forward, fundamentals, reference)만 복사하므로 mtime(FW001 완료 증명)이 보존되고, 수집기 트리에는 쓰지 않습니다 (unit의 `ReadOnlyPaths=/var/lib/hqa/data`). 다른 서버에서 받을 때만 `HQA_COLLECTOR_HOST`·`HQA_COLLECTOR_SSH_KEY`를 씁니다 (둘 중 하나만).
3. `deploy/strategy-runtime/strategy-runtime.env.example`의 이름대로 `/etc/hqa/strategy-runtime.env`(root:hqa 0640)를 만듭니다. 텔레그램 토큰은 여기에만 둡니다.
4. `bash deploy/strategy-runtime/install.sh`로 할 일을 확인하고, `--execute`로 unit 파일만 복사합니다. 활성화는 출력된 `systemctl enable --now ...`로 운영자가 직접 합니다.
5. 백엔드: `deploy/strategy-runtime/systemd/hqa-backend.service`(사용자 `hqa-backend`, `127.0.0.1:8000`만, `/etc/hqa/backend.env`, 이름은 `backend.env.example`). jar는 이 PC에서 `mvn -f backend/pom.xml -DskipTests package`로 만든 것을 `/opt/hqa-backend/hqa-backend-<commit>.jar`로 두고 `hqa-backend.jar` 링크를 바꿉니다. 비밀값(DB 비밀번호, 내부 토큰, KIS 암호화 키, SECRET_KEY)은 서버에서 `openssl rand`로 만들고 밖으로 복사하지 않습니다. 실행기 환경 파일의 `HQA_INTERNAL_TOKEN`은 백엔드와 같은 값입니다.
6. 처음 며칠은 `journalctl -u 'hqa-strategy-runtime-*'`와 `data/live/runs/`로 결과를 확인합니다.
