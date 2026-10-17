package com.hqa.backend.service;

import static com.hqa.backend.service.StrategyTestFixture.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.hqa.backend.dto.StrategyBatchRequest;
import com.hqa.backend.entity.StrategyAccount;
import com.hqa.backend.entity.StrategyBatch;
import com.hqa.backend.entity.StrategyBatchOrder;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class StrategyBatchServiceTest {
    private final StrategyTestFixture f = new StrategyTestFixture();

    @SuppressWarnings("unchecked")
    private static List<String> reasons(Map<String, Object> response) {
        return (List<String>) response.get("reasons");
    }

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> rejectedOrders(Map<String, Object> response) {
        return (List<Map<String, Object>>) response.get("rejectedOrders");
    }

    @Test
    void dryRunValidatesWithoutStoringAndSkipsTheWindowCheck() {
        f.clock.set("2026-11-02T07:00:00+09:00"); // before the OPEN window
        Map<String, Object> response = f.service.create(request(true, basket()));
        assertThat(response).containsEntry("status", "VALIDATED").containsEntry("batchId", null)
                .containsEntry("deduplicated", false).containsEntry("orderCount", 2);
        assertThat(reasons(response)).isEmpty();
        assertThat(f.batchRows).isEmpty();
        assertThat(f.orderRows).isEmpty();
        verify(f.batches, never()).saveAndFlush(any());
        verify(f.kis, never()).paperAuctionOrder(any(), any(), any(), any(), anyInt(), any());
    }

    @Test
    void missingDryRunFlagOnlyValidates() {
        StrategyBatchRequest base = request(false, basket());
        StrategyBatchRequest noFlag = new StrategyBatchRequest(base.strategyId(), base.userId(), base.decisionHash(),
                base.decisionDate(), base.sessionDate(), base.phase(), base.idempotencyKey(), null, 1L, base.orders());
        assertThat(f.service.create(noFlag)).containsEntry("status", "VALIDATED");
        assertThat(f.batchRows).isEmpty();
    }

    @Test
    void acceptedBatchIsQueuedWithPendingOrdersAndTheSameKeyReturnsTheStoredBatch() {
        Map<String, Object> first = f.service.create(request(false, basket()));
        assertThat(first).containsEntry("status", "QUEUED").containsEntry("deduplicated", false).containsEntry("orderCount", 2);
        String batchId = (String) first.get("batchId");
        StrategyBatch batch = f.batchRows.get(UUID.fromString(batchId));
        assertThat(batch.getAccountBinding()).isEqualTo("binding-1");
        assertThat(batch.getExpectedNavKrw()).isEqualTo(100_000_000L);
        assertThat(f.ordersOf(batch)).extracting(StrategyBatchOrder::getStatus).containsOnly("PENDING");
        assertThat(f.ordersOf(batch)).extracting(StrategyBatchOrder::getOrderType).containsOnly("MARKET");
        verify(f.guard).lock("u1");

        f.clock.set("2026-11-02T10:00:00+09:00"); // a retry after the window still returns the stored batch
        Map<String, Object> again = f.service.create(request(false, basket()));
        assertThat(again).containsEntry("batchId", batchId).containsEntry("deduplicated", true)
                .containsEntry("status", "QUEUED").containsEntry("orderCount", 2);
        assertThat(f.batchRows).hasSize(1);
        assertThat(f.orderRows).hasSize(2);
    }

    @Test
    void describeListsTheDocumentedFields() {
        StrategyBatch batch = f.queued(basket());
        Map<String, Object> view = f.service.describe(batch.getId());
        assertThat(view).containsEntry("batchId", batch.getId().toString()).containsEntry("strategyId", "hf001_f_both")
                .containsEntry("userId", "u1").containsEntry("phase", "OPEN").containsEntry("sessionDate", "2026-11-02")
                .containsEntry("status", "QUEUED");
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> rows = (List<Map<String, Object>>) view.get("orders");
        assertThat(rows.get(0)).containsEntry("seq", 1).containsEntry("stockCode", "005930").containsEntry("side", "SELL")
                .containsEntry("quantity", 10L).containsEntry("refPrice", 70_000L).containsEntry("status", "PENDING")
                .containsEntry("brokerOrderId", null).containsEntry("filledQuantity", 0L)
                .containsEntry("averageFillPrice", null).containsEntry("rejectReason", null);
    }

    @Test
    void unknownOrMismatchedStrategyAccountsAreRejectedBeforeAnyBrokerRead() {
        f.accountRows.clear();
        assertThat(reasons(f.service.create(request(false, basket())))).containsExactly("STRATEGY_ACCOUNT_NOT_FOUND");
        f.accountRows.put("u1", new StrategyAccount("u1", "av001_av_all", "paper", true, f.now()));
        assertThat(reasons(f.service.create(request(false, basket())))).containsExactly("STRATEGY_MISMATCH");
        verify(f.snapshots, never()).snapshot(anyString());
    }

    @Test
    void stageEnabledAndPaperAccountAreRequired() {
        f.accountRows.put("u1", new StrategyAccount("u1", "hf001_f_both", "shadow", false, f.now()));
        when(f.snapshots.paperUser("u1")).thenThrow(new IllegalArgumentException("PAPER_ACCOUNT_REQUIRED"));
        Map<String, Object> response = f.service.create(request(true, basket()));
        assertThat(response).containsEntry("status", "REJECTED").containsEntry("batchId", null);
        assertThat(reasons(response)).containsExactly("STRATEGY_STAGE_NOT_PAPER", "STRATEGY_ACCOUNT_DISABLED", "PAPER_ACCOUNT_REQUIRED");
        verify(f.snapshots, never()).snapshot(anyString());
    }

    @Test
    void missingKisSecretsAreReportedAsPaperAccountRequired() {
        when(f.snapshots.paperUser("u1")).thenThrow(new IllegalArgumentException("KIS_SECRET_MISSING"));
        assertThat(reasons(f.service.create(request(true, basket())))).containsExactly("PAPER_ACCOUNT_REQUIRED", "KIS_SECRET_MISSING");
    }

    @Test
    void sessionDateMustBeTodayAndAKrxSession() {
        assertThat(reasons(f.service.create(request(true, "OPEN", LocalDate.parse("2026-11-03"), basket()))))
                .containsExactly("SESSION_DATE_NOT_TODAY");
        f.clock.set("2026-11-07T08:35:00+09:00"); // Saturday
        assertThat(reasons(f.service.create(request(true, "OPEN", LocalDate.parse("2026-11-07"), basket()))))
                .containsExactly("NOT_A_KRX_SESSION");
        f.clock.set("2026-12-25T08:35:00+09:00"); // weekday closure
        assertThat(reasons(f.service.create(request(true, "OPEN", LocalDate.parse("2026-12-25"), basket()))))
                .containsExactly("NOT_A_KRX_SESSION");
    }

    @Test
    void storedBatchesNeedThePhaseWindow() {
        f.clock.set("2026-11-02T08:59:00+09:00"); // the window end is exclusive
        assertThat(reasons(f.service.create(request(false, basket())))).containsExactly("OUTSIDE_PHASE_WINDOW");
        assertThat(reasons(f.service.create(request(false, "CLOSE", SESSION, basket())))).containsExactly("OUTSIDE_PHASE_WINDOW");
        f.clock.set("2026-11-02T15:20:00+09:00");
        assertThat(f.service.create(request(false, "CLOSE", SESSION, basket()))).containsEntry("status", "QUEUED");
        assertThat(f.batchRows).hasSize(1);
    }

    @Test
    void haltSwitchRejectsEveryBatch() {
        f.halt("GLOBAL");
        assertThat(reasons(f.service.create(request(false, List.of(sell(1, "005930", 1, 70_000))))))
                .containsExactly("TRADING_HALTED");
        f.haltRows.clear();
        f.halt("u1");
        assertThat(reasons(f.service.create(request(true, basket())))).containsExactly("TRADING_HALTED");
        verify(f.snapshots, never()).snapshot(anyString());
    }

    @Test
    void dailyLossBlocksBuysButNotASellsOnlyBatch() {
        f.snapshot.put("dailyPnlPct", -2.0);
        Map<String, Object> response = f.service.create(request(false, basket()));
        assertThat(response).containsEntry("status", "REJECTED");
        assertThat(reasons(response)).containsExactly("DAILY_LOSS_LIMIT_EXCEEDED");
        assertThat(rejectedOrders(response)).containsExactly(Map.of("seq", 2, "reason", "DAILY_LOSS_LIMIT_EXCEEDED"));
        assertThat(f.service.create(request(false, List.of(sell(1, "005930", 10, 70_000))))).containsEntry("status", "QUEUED");
    }

    @Test
    void drawdownStopBlocksBuys() {
        f.peakRows.put("binding-1", 112_000_000L); // 100M equity is 10.7% below the peak
        Map<String, Object> response = f.service.create(request(true, basket()));
        assertThat(reasons(response)).containsExactly("DRAWDOWN_STOP");
        assertThat(rejectedOrders(response)).containsExactly(Map.of("seq", 2, "reason", "DRAWDOWN_STOP"));
    }

    @Test
    void unavailableAccountSnapshotRejectsTheBatch() {
        f.snapshot.clear();
        f.snapshot.putAll(Map.of("success", false, "error", "KIS_BALANCE_UNAVAILABLE"));
        assertThat(reasons(f.service.create(request(true, basket())))).containsExactly("ACCOUNT_SNAPSHOT_UNAVAILABLE");
    }

    @Test
    void dailyOrderLimitCountsStoredAndNewOrders() {
        f.properties.setMaxOrdersPerDay(3);
        f.queued(basket());
        f.clock.set("2026-11-02T15:21:00+09:00");
        Map<String, Object> response = f.service.create(request(false, "CLOSE", SESSION,
                List.of(sell(1, "000660", 1, 100_000), buy(2, "035420", 1, 100_000))));
        assertThat(reasons(response)).containsExactly("DAILY_ORDER_LIMIT_EXCEEDED");
    }

    @Test
    void cancelRatioAboveTheLimitRejectsNewBatches() {
        StrategyBatch first = f.queued(basket());
        f.ordersOf(first).forEach(order -> order.setCancelRequestedAt(f.now())); // 2 cancels / 2 orders > 0.5
        f.clock.set("2026-11-02T15:21:00+09:00");
        Map<String, Object> response = f.service.create(request(false, "CLOSE", SESSION, List.of(sell(1, "005930", 1, 70_000))));
        assertThat(reasons(response)).containsExactly("CANCEL_RATIO_EXCEEDED");
    }

    @Test
    void anOrderAboveTheEquityShareRejectsTheWholeBatch() {
        // 10% of 100M equity is 10M; 143 x 70,000 = 10.01M
        Map<String, Object> response = f.service.create(request(false, List.of(sell(1, "005930", 143, 70_000),
                buy(2, "0126Z0", 3, 51_000))));
        assertThat(response).containsEntry("status", "REJECTED").containsEntry("batchId", null);
        assertThat(reasons(response)).containsExactly("ORDER_EXCEEDS_EQUITY_SHARE");
        assertThat(rejectedOrders(response)).containsExactly(Map.of("seq", 1, "reason", "ORDER_EXCEEDS_EQUITY_SHARE"));
        assertThat(f.batchRows).isEmpty();
    }

    @Test
    void sellsMustPrecedeBuysAndOrdersMustBeUnique() {
        assertThat(reasons(f.service.create(request(true, List.of(buy(1, "0126Z0", 3, 51_000), sell(2, "005930", 10, 70_000))))))
                .containsExactly("SELLS_MUST_PRECEDE_BUYS");
        Map<String, Object> duplicate = f.service.create(request(true, List.of(sell(1, "005930", 1, 70_000), sell(2, "005930", 2, 70_000))));
        assertThat(reasons(duplicate)).containsExactly("DUPLICATE_ORDER");
        assertThat(rejectedOrders(duplicate)).containsExactly(Map.of("seq", 2, "reason", "DUPLICATE_ORDER"));
    }

    @Test
    void malformedOrdersAreInvalid() {
        List<StrategyBatchRequest.Order> bad = List.of(
                new StrategyBatchRequest.Order(1, "5930", "SELL", 1L, 70_000L, null),
                new StrategyBatchRequest.Order(2, "005930", "HOLD", 1L, 70_000L, null),
                new StrategyBatchRequest.Order(3, "000660", "BUY", 0L, 70_000L, null),
                new StrategyBatchRequest.Order(4, "035420", "BUY", 1L, 0L, null),
                new StrategyBatchRequest.Order(6, "051910", "BUY", 1L, 1L, Double.NaN),
                new StrategyBatchRequest.Order(7, "000270", "BUY", 1L, 1L, null),
                new StrategyBatchRequest.Order(7, "005380", "BUY", 1L, 1L, null)); // duplicate seq
        Map<String, Object> response = f.service.create(request(true, bad));
        assertThat(reasons(response)).containsExactly("INVALID_ORDER");
        assertThat(rejectedOrders(response)).hasSize(6).allSatisfy(row -> assertThat(row).containsEntry("reason", "INVALID_ORDER"));
    }

    @Test
    void batchSizeIsCapped() {
        f.properties.setMaxOrdersPerBatch(1);
        assertThat(reasons(f.service.create(request(true, basket())))).containsExactly("TOO_MANY_ORDERS");
        List<StrategyBatchRequest.Order> none = new ArrayList<>();
        assertThat(reasons(f.service.create(request(true, none)))).containsExactly("NO_ORDERS");
    }

    @Test
    void malformedRequestsAndForeignKeysAreRejected() {
        StrategyBatchRequest base = request(false, basket());
        assertThat(reasons(f.service.create(new StrategyBatchRequest(base.strategyId(), base.userId(), "xyz", base.decisionDate(),
                base.sessionDate(), base.phase(), base.idempotencyKey(), false, 1L, base.orders())))).containsExactly("INVALID_REQUEST");
        assertThat(reasons(f.service.create(new StrategyBatchRequest(base.strategyId(), base.userId(), base.decisionHash(),
                base.decisionDate(), base.sessionDate(), "MIDDAY", base.idempotencyKey(), false, 1L, base.orders()))))
                .containsExactly("INVALID_REQUEST");
        assertThat(reasons(f.service.create(new StrategyBatchRequest(base.strategyId(), base.userId(), base.decisionHash(),
                base.decisionDate(), base.sessionDate(), base.phase(), "b".repeat(64), false, 1L, base.orders()))))
                .containsExactly("IDEMPOTENCY_KEY_MISMATCH");
        assertThat(f.batchRows).isEmpty();
    }

    @Test
    void aSecondBatchForTheSamePhaseIsRejectedEvenWithANewDecisionHash() {
        StrategyBatch first = f.queued(basket());
        Map<String, Object> second = f.service.create(request(false, "OPEN", SESSION, "b".repeat(64), basket()));
        assertThat(second).containsEntry("status", "REJECTED").containsEntry("batchId", null);
        assertThat(reasons(second)).containsExactly("BATCH_ALREADY_EXISTS_FOR_PHASE");
        assertThat(reasons(f.service.create(request(true, "OPEN", SESSION, "b".repeat(64), basket()))))
                .containsExactly("BATCH_ALREADY_EXISTS_FOR_PHASE");
        assertThat(f.batchRows).containsOnlyKeys(first.getId());
        // A batch finalized as REJECTED does not hold the phase.
        first.setStatus("REJECTED");
        assertThat(f.service.create(request(false, "OPEN", SESSION, "b".repeat(64), basket()))).containsEntry("status", "QUEUED");
    }

    @Test
    void theLockedInsertRechecksThePhase() {
        // Another request stored a batch for the phase between the unlocked checks and the account lock.
        when(f.guard.lock("u1")).thenAnswer(inv -> {
            StrategyBatch other = new StrategyBatch();
            other.setUserId("u1");
            other.setStrategyId("hf001_f_both");
            other.setSessionDate(SESSION);
            other.setPhase("OPEN");
            other.setStatus("QUEUED");
            other.setIdempotencyKey("c".repeat(64));
            other.setCreatedAt(f.now());
            f.batchRows.put(other.getId(), other);
            return f.user;
        });
        assertThat(reasons(f.service.create(request(false, basket())))).containsExactly("BATCH_ALREADY_EXISTS_FOR_PHASE");
        assertThat(f.batchRows).hasSize(1);
    }

    @Test
    void unverifiedCalendarDatesFailClosed() {
        f.clock.set("2026-11-19T08:35:00+09:00"); // CSAT day, special hours not configured
        assertThat(reasons(f.service.create(request(true, "OPEN", LocalDate.parse("2026-11-19"), basket()))))
                .containsExactly("CALENDAR_UNVERIFIED");
        f.clock.set("2027-10-04T08:35:00+09:00"); // after the reviewed horizon
        assertThat(reasons(f.service.create(request(true, "OPEN", LocalDate.parse("2027-10-04"), basket()))))
                .containsExactly("CALENDAR_UNVERIFIED");
        f.properties.setCalendarReviewedThrough("2027-12-31");
        assertThat(f.service.create(request(true, "OPEN", LocalDate.parse("2027-10-04"), basket()))).containsEntry("status", "VALIDATED");
        assertThat(f.sessions.phaseWindow(LocalDate.parse("2026-11-19"), "OPEN")).isNull();

        StrategySessionClock configured = new StrategySessionClock(new KrxSessionCalendar("", "2026-11-19@10:00-16:30"), f.properties);
        assertThat(configured.calendarVerified(LocalDate.parse("2026-11-19"))).isTrue();
    }

    @Test
    void idempotencyKeyIsTheDocumentedSha256() {
        // sha256("hf001_f_both|<hash>|2026-11-02|OPEN|u1")
        assertThat(StrategyBatchService.idempotencyKey("s", "h", "2026-11-02", "OPEN", "u"))
                .isEqualTo(sha256Hex("s|h|2026-11-02|OPEN|u")).hasSize(64);
    }

    private static String sha256Hex(String text) {
        try {
            return java.util.HexFormat.of().formatHex(java.security.MessageDigest.getInstance("SHA-256")
                    .digest(text.getBytes(java.nio.charset.StandardCharsets.UTF_8)));
        } catch (java.security.NoSuchAlgorithmException ex) {
            throw new IllegalStateException(ex);
        }
    }
}
