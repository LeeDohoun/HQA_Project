package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.dto.InternalTradeSignalRequest;
import com.hqa.backend.entity.*;
import com.hqa.backend.repository.*;
import java.time.OffsetDateTime;
import java.util.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

class PaperTradeStoreTest {
    private final TradeSignalRepository signals = mock(TradeSignalRepository.class);
    private final TradeSignalExecutionRepository executions = mock(TradeSignalExecutionRepository.class);
    private final PaperAccountGuard accountGuard = mock(PaperAccountGuard.class);
    private final TradePlanReceiptRepository receipts = mock(TradePlanReceiptRepository.class);
    private final PaperTradeStore store = new PaperTradeStore(signals, executions, accountGuard, new ObjectMapper(), receipts, 1, 0.5);
    private final OffsetDateTime now = OffsetDateTime.parse("2026-09-04T10:00:00+09:00");
    private final User user = user();
    private final Map<String, TradeSignalExecution> savedExecutions = new LinkedHashMap<>();

    @BeforeEach
    void setup() {
        when(accountGuard.lock("u1")).thenReturn(user);
        when(accountGuard.binding(user)).thenAnswer(inv -> user.getSecret().getKisAccountNo());
        when(signals.ownerOf(anyString())).thenReturn(Optional.of("u1"));
        when(executions.ownerOf(anyString())).thenReturn(Optional.of("u1"));
        when(signals.saveAndFlush(any())).thenAnswer(inv -> {
            TradeSignal signal = inv.getArgument(0);
            if (signal.getId() == null) ReflectionTestUtils.setField(signal, "id", "s1");
            when(signals.findById(signal.getId())).thenReturn(Optional.of(signal));
            return signal;
        });
        when(executions.saveAndFlush(any())).thenAnswer(inv -> {
            TradeSignalExecution execution = inv.getArgument(0);
            if (execution.getId() == null) ReflectionTestUtils.setField(execution, "id", "e" + (savedExecutions.size() + 1));
            savedExecutions.put(execution.getId(), execution);
            when(executions.findById(execution.getId())).thenReturn(Optional.of(execution));
            return execution;
        });
        when(executions.findBySignalId(anyString())).thenAnswer(inv -> savedExecutions.values().stream()
                .filter(e -> e.getSignalId().equals(inv.getArgument(0))).toList());
        when(executions.findByUserIdAndStatusIn(eq("u1"), anyList())).thenAnswer(inv -> savedExecutions.values().stream()
                .filter(e -> ((List<?>) inv.getArgument(1)).contains(e.getStatus())).toList());
    }

    private TradeSignalExecution unknownOrder(TradeSignal signal) {
        TradeSignalExecution execution = new TradeSignalExecution();
        execution.setSignalId(signal.getId());
        execution.setUserId("u1");
        execution.setStatus("UNKNOWN");
        execution.setReservedCash(50_000L);
        execution.setRejectReason("ORDER_ACCEPTANCE_UNKNOWN");
        execution.setKisResponse("{\"success\":false,\"unknown\":true}");
        return executions.saveAndFlush(execution);
    }

    @Test
    void operatorResolutionReleasesOnlyAnUnknownOrderAndKeepsTheRecord() throws Exception {
        TradeSignal signal = store.save(request("HOLD", 10, 1, "a", now), account(12), now);
        signal.setRejectReason("ORDER_RECONCILIATION_REQUIRED");
        TradeSignalExecution adopted = unknownOrder(signal);
        store.adoptBrokerOrder(adopted.getId(), "0000117057", "06010", "KIS 앱 주문내역 확인", now);
        assertThat(adopted.getStatus()).isEqualTo("ORDER_SUBMITTED");
        assertThat(adopted.getOrderId()).isEqualTo("0000117057");
        assertThat(adopted.getReservedCash()).isEqualTo(50_000L);      // reconciliation settles it from the fills
        assertThat(signal.getRejectReason()).isNull();
        Map<String, Object> record = new ObjectMapper().readValue(adopted.getKisResponse(),
                new com.fasterxml.jackson.core.type.TypeReference<Map<String, Object>>() { });
        @SuppressWarnings("unchecked")
        Map<String, Object> resolution = (Map<String, Object>) record.get("operatorResolution");
        assertThat(resolution).containsEntry("action", "ADOPTED_BROKER_ORDER").containsEntry("note", "KIS 앱 주문내역 확인");
        assertThat(record.get("previousResponse")).isEqualTo("{\"success\":false,\"unknown\":true}");
        assertThatThrownBy(() -> store.confirmNotSubmitted(adopted.getId(), "again", now))
                .hasMessage("EXECUTION_NOT_AWAITING_OPERATOR");

        TradeSignalExecution unsent = unknownOrder(signal);
        store.confirmNotSubmitted(unsent.getId(), "주문내역에 없음", now);
        assertThat(unsent.getStatus()).isEqualTo("REJECTED");
        assertThat(unsent.getRejectReason()).isEqualTo("OPERATOR_CONFIRMED_NOT_SUBMITTED");
        assertThat(unsent.getReservedCash()).isZero();
    }

    @Test
    void backendCapAndMonitorCapacityRejectNewEntryBeforeMutation() {
        assertThatThrownBy(() -> store.save(request("BUY", 25, 1, "a", now), account(0), now))
                .hasMessageContaining("20 percent");
        List<Map<String, Object>> holdings = new ArrayList<>();
        for (int i = 0; i < 10; i++) holdings.add(Map.of("stockCode", String.format("%06d", i), "quantity", 1));
        Map<String, Object> overloaded = account(0);
        overloaded.put("holdings", holdings);
        assertThatThrownBy(() -> store.save(request("BUY", 10, 1, "a", now), overloaded, now))
                .hasMessage("PAPER_MONITOR_CAPACITY_EXCEEDED");
        verify(signals, never()).saveAndFlush(any());
    }

    @Test
    void holdAdoptsActualHoldingAndIgnoresEntryExpiry() {
        TradeSignal signal = store.save(request("HOLD", 10, 1, "a", now), account(12), now);
        assertThat(signal.getStatus()).isEqualTo("OPEN");
        assertThat(signal.getManagedQuantity()).isEqualTo(12);
        assertThat(signal.getAccountBinding()).isEqualTo(accountGuard.binding(user));
        assertThatThrownBy(() -> store.save(request("HOLD", 10, 1, "b", now), account(0), now))
                .hasMessageContaining("actual holding");
    }

    @Test
    void historicalReceiptMakesAThenBThenAReplayReturnCurrentPlan() {
        Map<String, TradePlanReceipt> saved = new HashMap<>();
        when(receipts.findById(anyString())).thenAnswer(inv -> Optional.ofNullable(saved.get(inv.getArgument(0))));
        when(receipts.saveAndFlush(any())).thenAnswer(inv -> {
            TradePlanReceipt receipt = inv.getArgument(0);
            String key = (String) ReflectionTestUtils.getField(receipt, "id");
            saved.put(key, receipt);
            return receipt;
        });
        TradeSignal a = store.save(request("HOLD", 10, 1, "a", now.minusSeconds(2)), account(10), now);
        when(signals.findByUserIdAndStatusIn("u1", PaperTradeStore.ACTIVE)).thenReturn(List.of(a));
        TradeSignal b = store.save(request("HOLD", 10, 2, "b", now), account(10), now);
        TradeSignal replay = store.save(request("HOLD", 10, 3, "a", now.minusSeconds(2)), account(10), now);
        assertThat(replay).isSameAs(b);
        assertThat(replay.getPlanVersion()).isEqualTo(2);
        assertThat(replay.getAnalysisAsOf()).isEqualTo(now);
        assertThatThrownBy(() -> store.save(request("HOLD", 10, 3, "c", now.minusSeconds(1)), account(10), now))
                .hasMessage("STALE_ANALYSIS");
    }

    @Test
    void staleAndFutureAnalysesCannotReplaceProtection() {
        assertThatThrownBy(() -> store.save(request("HOLD", 10, 1, "a", now.minusMinutes(16)), account(10), now))
                .hasMessageContaining("fresh analysisId");
        assertThatThrownBy(() -> store.save(request("HOLD", 10, 1, "a", now.plusSeconds(6)), account(10), now))
                .hasMessageContaining("fresh analysisId");
        verify(signals, never()).saveAndFlush(any());
    }

    @Test
    void conditionalStopCannotAuthorizeBuyAndOpenStopCannotBeLowered() {
        assertThatThrownBy(() -> store.save(request("BUY", 10, 1, "a", now, 90, true), account(0), now))
                .hasMessage("BUY_REQUIRES_MATCHING_UNCONDITIONAL_PRICE_STOP");
        TradeSignal open = store.save(request("HOLD", 10, 1, "a", now.minusSeconds(1)), account(10), now);
        when(signals.findByUserIdAndStatusIn("u1", PaperTradeStore.ACTIVE)).thenReturn(List.of(open));
        assertThatThrownBy(() -> store.save(request("HOLD", 10, 2, "b", now, 89, false), account(10), now))
                .hasMessage("OPEN_PLAN_HARD_STOP_CANNOT_BE_WEAKENED");
        assertThat(open.getPlanVersion()).isEqualTo(1);
        assertThat(open.getStatus()).isEqualTo("OPEN");
    }

    @Test
    void staleExpiryObservationCannotExpireNewVersion() {
        TradeSignal plan = store.save(request("BUY", 10, 2, "a", now), account(0), now);
        store.expireEntry(plan.getId(), 1, true, now);
        assertThat(plan.getStatus()).isEqualTo("WAITING_ENTRY");
        store.expireEntry(plan.getId(), 2, false, now);
        assertThat(plan.getStatus()).isEqualTo("WAITING_ENTRY");
        store.expireEntry(plan.getId(), 2, true, now);
        assertThat(plan.getStatus()).isEqualTo("EXPIRED");
    }

    @Test
    void entryValidityIsBoundedByAnalysisAndMustPrecedePlannedExit() throws Exception {
        assertThatThrownBy(() -> store.save(request("BUY", 10, 1, "a", now.minusMinutes(6)), account(0), now))
                .hasMessage("ENTRY_VALIDITY_MUST_BE_WITHIN_ANALYSIS_15_MINUTES");
        ObjectMapper mapper = new ObjectMapper().findAndRegisterModules();
        com.fasterxml.jackson.databind.node.ObjectNode json = mapper.valueToTree(request("BUY", 10, 1, "a", now));
        json.put("plannedExitAt", now.plusMinutes(9).toString());
        InternalTradeSignalRequest invalid = mapper.treeToValue(json, InternalTradeSignalRequest.class);
        assertThatThrownBy(() -> store.save(invalid, account(0), now)).hasMessage("PLANNED_EXIT_MUST_FOLLOW_ENTRY_VALIDITY");
    }

    @Test
    void entryAtStopAndStaleAccountSnapshotDoNotCreateOrderIntents() {
        TradeSignal plan = store.save(request("BUY", 10, 1, "a", now), account(0), now);
        assertThatThrownBy(() -> store.claim(plan.getId(), 1, TradeConditions.TriggerType.ENTRY, "entry", account(0),
                90, 10000, 100, null, now)).hasMessage("ENTRY_ALREADY_AT_OR_BELOW_STOP");
        Map<String, Object> stale = account(0);
        stale.put("capturedAt", now.minusSeconds(21).toString());
        assertThatThrownBy(() -> store.claim(plan.getId(), 1, TradeConditions.TriggerType.ENTRY, "entry", stale,
                100, 10000, 100, null, now)).hasMessage("ACCOUNT_SNAPSHOT_STALE");
        assertThat(savedExecutions).isEmpty();
    }

    @Test
    void delayedInquiryCannotResurrectTerminalOrder() {
        TradeSignal plan = store.save(request("BUY", 10, 1, "a", now), account(0), now);
        TradeSignalExecution intent = store.claim(plan.getId(), 1, TradeConditions.TriggerType.ENTRY, "entry", account(0),
                100, 10000, 100, null, now);
        store.observeFill(intent.getId(), 2, 100, 0, true, "org", now);
        store.observeFill(intent.getId(), 2, 100, 8, false, "org", now);
        assertThat(intent.getStatus()).isEqualTo("CANCELLED");
        assertThat(intent.getReservedCash()).isZero();
        assertThat(plan.getManagedQuantity()).isEqualTo(2);
        assertThat(plan.getStatus()).isEqualTo("OPEN");
    }

    @Test
    void sizingUsesPowerCashAndReservationsInsteadOfOneShare() {
        TradeSignal signal = store.save(request("BUY", 20, 1, "a", now), account(0), now);
        when(executions.reservedCashForUser("u1")).thenReturn(1500L);
        var intent = store.claim(signal.getId(), 1, TradeConditions.TriggerType.ENTRY, "entry", account(0), 100,
                3000, 30, null, now);
        assertThat(intent.getSubmittedQuantity()).isEqualTo(15);
        assertThat(intent.getReservedCash()).isEqualTo(1500);
        assertThat(intent.getStatus()).isEqualTo("INTENT");
        assertThatThrownBy(() -> store.claim(signal.getId(), 1, TradeConditions.TriggerType.ENTRY, "entry", account(0),
                100, 3000, 30, null, now)).hasMessage("ORDER_RECONCILIATION_REQUIRED");
    }

    @Test
    void reductionIsBoundedByHoldingAndSellableQuantity() {
        TradeSignal signal = store.save(request("HOLD", 10, 1, "a", now), account(11), now);
        var intent = store.claim(signal.getId(), 1, TradeConditions.TriggerType.REDUCE, "reduce", account(11),
                100, 0, 0, 0.5, now);
        assertThat(intent.getOrderSide()).isEqualTo("SELL");
        assertThat(intent.getSubmittedQuantity()).isEqualTo(5);
        store.observeFill(intent.getId(), 5, 100, 0, false, "org", now);
        assertThat(signal.getStatus()).isEqualTo("OPEN");
        assertThat(signal.getManagedQuantity()).isEqualTo(6);
        assertThatThrownBy(() -> store.claim(signal.getId(), 1, TradeConditions.TriggerType.REDUCE, "reduce", account(6),
                100, 0, 0, 0.5, now)).hasMessage("TRIGGER_ALREADY_CONSUMED");
    }

    @Test
    void partialEntryIsProtectedAndCancelRequiresBrokerConfirmation() {
        TradeSignal signal = store.save(request("BUY", 20, 1, "a", now), account(0), now);
        var intent = store.claim(signal.getId(), 1, TradeConditions.TriggerType.ENTRY, "entry", account(0),
                100, 10000, 100, null, now);
        store.acknowledge(intent.getId(), Map.of("success", true, "response", Map.of("rt_cd", "0", "output", Map.of("ODNO", "order1"))));
        store.observeFill(intent.getId(), 2, 100, intent.getSubmittedQuantity() - 2, false, "org", now);
        assertThat(signal.getStatus()).isEqualTo("OPEN");
        store.expireEntry(signal.getId(), signal.getPlanVersion(), true, now);
        assertThat(signal.getStatus()).isEqualTo("OPEN");
        assertThat(store.markCancelRequested(intent.getId())).isTrue();
        assertThat(store.markCancelRequested(intent.getId())).isFalse();
        assertThat(intent.getReservedCash()).isPositive();
        store.observeFill(intent.getId(), 2, 100, 0, true, "org", now);
        assertThat(intent.getStatus()).isEqualTo("CANCELLED");
        assertThat(intent.getReservedCash()).isZero();
        assertThat(signal.getManagedQuantity()).isEqualTo(2);
        assertThat(signal.getStatus()).isEqualTo("OPEN");
    }

    @Test
    void unknownOrderRetainsReservationAndNeverAdmitsAnotherEntry() {
        TradeSignal signal = store.save(request("BUY", 20, 1, "a", now), account(0), now);
        var intent = store.claim(signal.getId(), 1, TradeConditions.TriggerType.ENTRY, "entry", account(0),
                100, 10000, 100, null, now);
        store.acknowledge(intent.getId(), Map.of("success", false, "error", "timeout"));
        assertThat(intent.getStatus()).isEqualTo("UNKNOWN");
        assertThat(intent.getReservedCash()).isPositive();
        assertThat(signal.getRejectReason()).isEqualTo("ORDER_ACCEPTANCE_UNKNOWN");
        assertThatThrownBy(() -> store.claim(signal.getId(), 1, TradeConditions.TriggerType.ENTRY, "entry", account(0),
                100, 10000, 100, null, now)).hasMessage("ORDER_RECONCILIATION_REQUIRED");
    }

    @Test
    void changedAccountOrStaleVersionCannotClaimOldPlan() {
        TradeSignal signal = store.save(request("BUY", 10, 1, "a", now), account(0), now);
        assertThatThrownBy(() -> store.claim(signal.getId(), 2, TradeConditions.TriggerType.ENTRY, "entry", account(0),
                100, 10000, 100, null, now)).hasMessage("STALE_PLAN_VERSION");
        user.getSecret().setKisAccountNo("changed-account");
        assertThatThrownBy(() -> store.claim(signal.getId(), 1, TradeConditions.TriggerType.ENTRY, "entry", account(0),
                100, 10000, 100, null, now)).hasMessage("ACCOUNT_BINDING_CHANGED");
    }

    @Test
    void transientRejectionsCanBeRetriedButBrokerRejectionsConsumeTheTrigger() {
        TradeSignal signal = store.save(request("HOLD", 10, 1, "a", now), account(10), now);
        var limited = store.claim(signal.getId(), 1, TradeConditions.TriggerType.REDUCE, "reduce", account(10),
                100, 0, 0, 0.5, now);
        store.acknowledge(limited.getId(), Map.of("success", false, "response",
                Map.of("rt_cd", "1", "msg_cd", "EGW00201", "msg1", "too many requests")));
        assertThat(limited.getStatus()).isEqualTo("REJECTED");
        assertThat(limited.getRejectReason()).isEqualTo("KIS_RATE_LIMITED");
        var notSent = store.claim(signal.getId(), 1, TradeConditions.TriggerType.REDUCE, "reduce", account(10),
                100, 0, 0, 0.5, now);
        store.acknowledge(notSent.getId(), Map.of("success", false, "notSent", true, "error", "queue"));
        assertThat(notSent.getRejectReason()).isEqualTo("ORDER_NOT_SENT_RATE_QUEUE_FULL");
        var judged = store.claim(signal.getId(), 1, TradeConditions.TriggerType.REDUCE, "reduce", account(10),
                100, 0, 0, 0.5, now);
        store.acknowledge(judged.getId(), Map.of("success", false, "response", Map.of("rt_cd", "1", "msg_cd", "APBK0013")));
        assertThat(judged.getRejectReason()).isEqualTo("KIS_ORDER_REJECTED");
        assertThatThrownBy(() -> store.claim(signal.getId(), 1, TradeConditions.TriggerType.REDUCE, "reduce", account(10),
                100, 0, 0, 0.5, now)).hasMessage("TRIGGER_ALREADY_CONSUMED");
    }

    private InternalTradeSignalRequest reduceHold(int version, double fraction, OffsetDateTime plannedExit) {
        Map<String, Object> payload = Map.of("schema_version", 2,
                "exit_conditions", List.of(Map.of("id", "stop", "all", List.of(Map.of("field", "current_price", "operator", "<=", "value", 90)))),
                "reduce_conditions", List.of(Map.of("id", "trim", "reduce_fraction", fraction,
                        "all", List.of(Map.of("field", "pnl_rate", "operator", ">=", "value", 5)))));
        OffsetDateTime asOf = now.plusSeconds(version);
        return new InternalTradeSignalRequest("u1", "luna", "short", null, null, "005930", "Samsung", "HOLD",
                null, null, "MEDIUM", "10%", 100L, "90", "reason", asOf.plusMinutes(10), Map.of(), Map.of("stop_loss_price", 90), payload,
                "hold-" + version, version, asOf.plusMinutes(10), plannedExit, 10.0, "PAPER", "hold-" + version, asOf);
    }

    @Test
    void aReductionRunsOnceAcrossReissuedPlanVersionsUnlessItsContentChanges() {
        TradeSignal signal = store.save(reduceHold(1, 0.5, now.plusDays(3)), account(10), now);
        when(signals.findByUserIdAndStatusIn("u1", PaperTradeStore.ACTIVE)).thenReturn(List.of(signal));
        var first = store.claim(signal.getId(), 1, TradeConditions.TriggerType.REDUCE, "trim", account(10), 100, 0, 0, 0.5, now);
        store.acknowledge(first.getId(), Map.of("success", true, "response", Map.of("rt_cd", "0", "output", Map.of("ODNO", "o1"))));
        store.observeFill(first.getId(), 5, 100, 0, false, "org", now);
        store.save(reduceHold(2, 0.5, now.plusDays(3)), account(5), now.plusSeconds(2));
        assertThatThrownBy(() -> store.claim(signal.getId(), 2, TradeConditions.TriggerType.REDUCE, "trim", account(5),
                100, 0, 0, 0.5, now.plusSeconds(2))).hasMessage("TRIGGER_ALREADY_CONSUMED");
        store.save(reduceHold(3, 0.25, now.plusDays(3)), account(5), now.plusSeconds(3));  // a different reduction tier
        assertThat(store.claim(signal.getId(), 3, TradeConditions.TriggerType.REDUCE, "trim", account(5),
                100, 0, 0, 0.25, now.plusSeconds(3)).getSubmittedQuantity()).isEqualTo(1);
    }

    @Test
    void aHeldPositionsPlannedExitNeverMovesLater() {
        TradeSignal signal = store.save(reduceHold(1, 0.5, now.plusDays(3)), account(10), now);
        when(signals.findByUserIdAndStatusIn("u1", PaperTradeStore.ACTIVE)).thenReturn(List.of(signal));
        store.save(reduceHold(2, 0.5, now.plusDays(5)), account(10), now.plusSeconds(2));
        assertThat(signal.getPlanVersion()).isEqualTo(2);  // the plan was replaced in place
        assertThat(signal.getPlannedExitAt()).isEqualTo(now.plusDays(3));
        store.save(reduceHold(3, 0.5, now.plusDays(2)), account(10), now.plusSeconds(3));
        assertThat(signal.getPlannedExitAt()).isEqualTo(now.plusDays(2));
    }

    @Test
    void autoTradeOffBlocksNewEntriesButKeepsProtectiveSells() {
        TradeSignal held = store.save(reduceHold(1, 0.5, now.plusDays(3)), account(10), now);
        user.setAutoTradeEnabled(false);
        assertThat(store.claim(held.getId(), 1, TradeConditions.TriggerType.EXIT, "stop", account(10), 90, 0, 0, null, now)
                .getOrderSide()).isEqualTo("SELL");
        user.setAutoTradeEnabled(true);
    }

    static User user() {
        User user = new User();
        user.setUserId("u1");
        user.setAutoTradeEnabled(true);
        UserSecret secret = new UserSecret();
        secret.setKisAppKey("encrypted-key");
        secret.setKisAppSecret("encrypted-secret");
        secret.setKisAccountNo("encrypted-account");
        secret.setKisAccountProductCode("01");
        user.setSecret(secret);
        return user;
    }
    static Map<String, Object> account(int quantity) {
        Map<String, Object> account = new HashMap<>(Map.of("success", true, "userId", "u1", "equity", 10000L,
                "orderableCash", 10000L, "reservedCash", 0L, "dailyPnlPct", 0.0, "entryEligible", true));
        account.put("capturedAt", "2026-09-04T10:00:00+09:00");
        account.put("holdings", quantity == 0 ? List.of() : List.of(Map.of("stockCode", "005930", "quantity", quantity,
                "sellableQuantity", quantity, "avgPrice", 100.0, "currentPrice", 100, "evalAmount", quantity * 100L, "pnlRate", -10.0)));
        return account;
    }
    private InternalTradeSignalRequest request(String action, double target, int version, String id, OffsetDateTime asOf) {
        return request(action, target, version, id, asOf, 90, false);
    }
    private InternalTradeSignalRequest request(String action, double target, int version, String id, OffsetDateTime asOf,
            double stop, boolean gated) {
        Map<String, Object> condition = Map.of("field", "current_price", "operator", ">=", "value", 100);
        Map<String, Object> exit = Map.of("field", "current_price", "operator", "<=", "value", stop);
        List<Map<String, Object>> stopPredicates = gated ? List.of(exit, Map.of("field", "market_time", "operator", ">=", "value", "14:00:00"))
                : List.of(exit);
        Map<String, Object> payload = Map.of("schema_version", 2, "entry_conditions", List.of(Map.of("id", "entry", "all", List.of(condition))),
                "exit_conditions", List.of(Map.of("id", "stop", "all", stopPredicates)));
        return new InternalTradeSignalRequest("u1", "luna", "short", null, null, "005930", "Samsung", action,
                null, null, "MEDIUM", target + "%", 100L, Double.toString(stop), "reason", now.plusMinutes(10), Map.of(), Map.of("stop_loss_price", stop), payload, id,
                version, now.plusMinutes(10), now.plusDays(3), target, "PAPER", id, asOf);
    }

    @Test
    void explicitCancelRejectionCanBeRetriedAfterFreshBrokerObservation() {
        TradeSignal signal = store.save(request("BUY", 20, 1, "a", now), account(0), now);
        var intent = store.claim(signal.getId(), 1, TradeConditions.TriggerType.ENTRY, "entry", account(0),
                100, 10000, 100, null, now);
        store.acknowledge(intent.getId(), Map.of("success", true, "response", Map.of("rt_cd", "0", "output", Map.of("ODNO", "order1"))));
        store.observeFill(intent.getId(), 2, 100, intent.getSubmittedQuantity() - 2, false, "org", now);
        long reserved = intent.getReservedCash();
        assertThat(store.markCancelRequested(intent.getId())).isTrue();
        store.acknowledgeCancellation(intent.getId(), Map.of("success", false, "response", Map.of("rt_cd", "1")));
        assertThat(intent.getStatus()).isEqualTo("PARTIALLY_FILLED");
        assertThat(intent.getReservedCash()).isEqualTo(reserved);
        store.observeFill(intent.getId(), 2, 100, intent.getSubmittedQuantity() - 2, false, "org", now);
        assertThat(store.markCancelRequested(intent.getId())).isTrue();
    }

    @Test
    void unknownCancellationNeverAuthorizesBlindResubmission() {
        TradeSignal signal = store.save(request("BUY", 20, 1, "a", now), account(0), now);
        var intent = store.claim(signal.getId(), 1, TradeConditions.TriggerType.ENTRY, "entry", account(0),
                100, 10000, 100, null, now);
        store.acknowledge(intent.getId(), Map.of("success", true, "response", Map.of("rt_cd", "0", "output", Map.of("ODNO", "order1"))));
        store.markCancelRequested(intent.getId());
        store.acknowledgeCancellation(intent.getId(), Map.of("success", false, "unknown", true));
        assertThat(store.markCancelRequested(intent.getId())).isFalse();
        assertThat(intent.getReservedCash()).isPositive();
        store.observeFill(intent.getId(), 0, 0, 0, true, "org", now);
        store.acknowledgeCancellation(intent.getId(), Map.of("success", false, "response", Map.of("rt_cd", "1")));
        assertThat(intent.getStatus()).isEqualTo("CANCELLED");
    }
}
