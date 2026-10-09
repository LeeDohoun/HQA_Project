package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.hqa.backend.entity.TradeSignal;
import com.hqa.backend.entity.TradeSignalExecution;
import com.hqa.backend.entity.User;
import com.hqa.backend.repository.*;
import java.time.Clock;
import java.time.OffsetDateTime;
import java.util.*;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

class PaperTradeLifecycleTest {
    private final TradeSignalRepository signals = mock(TradeSignalRepository.class);
    private final TradeSignalExecutionRepository executions = mock(TradeSignalExecutionRepository.class);
    private final PaperAccountSnapshotService accounts = mock(PaperAccountSnapshotService.class);
    private final PaperTradeStore store = mock(PaperTradeStore.class);
    private final KisClient kis = mock(KisClient.class);
    private final OffsetDateTime now = OffsetDateTime.parse("2026-09-04T10:00:00+09:00");
    private final ObjectMapper mapper = new ObjectMapper();
    private final PaperTradeLifecycle lifecycle = new PaperTradeLifecycle(signals, executions, accounts, store, kis,
            mapper, Clock.fixed(now.toInstant(), now.getOffset()));
    private final User user = PaperTradeStoreTest.user();
    private final TradeSignal signal = new TradeSignal();

    @BeforeEach
    void setup() throws Exception {
        ReflectionTestUtils.setField(signal, "id", "s1");
        signal.setUserId("u1");
        signal.setStockCode("005930");
        signal.setStatus("OPEN");
        signal.setAccountBinding("binding");
        signal.setPlanVersion(1);
        signal.setManagedQuantity(10);
        signal.setEntryValidUntil(now.minusMinutes(1));
        signal.setConditionPayload(mapper.writeValueAsString(Map.of("schema_version", 2, "exit_conditions", List.of(
                Map.of("id", "stop", "all", List.of(Map.of("field", "pnl_rate", "operator", "<=", "value", -5)))))));
        when(signals.findById("s1")).thenReturn(Optional.of(signal));
        when(accounts.paperUser("u1")).thenReturn(user);
        when(accounts.binding(user)).thenReturn("binding");
        when(accounts.snapshot("u1")).thenReturn(PaperTradeStoreTest.account(10));
        when(kis.fetchAccessToken("u1", user.getSecret())).thenReturn("token");
        when(kis.inquireCurrentPrice("u1", user.getSecret(), "token", "005930")).thenReturn(90L);
        doAnswer(inv -> { signal.setRejectReason(inv.getArgument(1)); return null; }).when(store).block(eq("s1"), anyString());
    }

    @Test
    void exitUsesFreshPricePnlAndDoesNotRequireEntryRiskOrPower() {
        Map<String, Object> snapshot = PaperTradeStoreTest.account(10);
        snapshot.put("entryEligible", false);
        snapshot.put("dailyPnlPct", null);
        snapshot.put("holdings", List.of(Map.of("stockCode", "005930", "quantity", 10, "avgPrice", 100.0,
                "pnlRate", 2.0, "sellableQuantity", 10)));
        when(accounts.snapshot("u1")).thenReturn(snapshot);
        TradeSignalExecution intent = execution();
        when(store.claim(eq("s1"), eq(1), eq(TradeConditions.TriggerType.EXIT), eq("stop"), same(snapshot), eq(90L),
                eq(0L), eq(0L), isNull(), eq(now))).thenReturn(intent);
        when(kis.paperOrder(anyString(), any(), anyString(), anyString(), anyInt(), anyLong(), anyString()))
                .thenReturn(Map.of("success", true));
        doAnswer(inv -> { intent.setStatus("ORDER_SUBMITTED"); return null; }).when(store).acknowledge(any(), anyMap());
        when(executions.findBySignalId("s1")).thenReturn(List.of(intent));
        assertThat(lifecycle.triggerResponse("s1", request("EXIT", 1, "stop"))).containsEntry("accepted", true)
                .containsEntry("executionStatus", "ORDER_SUBMITTED").containsEntry("rejectReason", null);
        assertThat(signal.getStatus()).isEqualTo("OPEN");
        verify(kis, never()).paperPurchasingPower(anyString(), any(), anyString(), anyString(), anyLong());
    }

    @Test
    void staleAndFractionalVersionsAreExplicitlyRejectedWithoutBrokerCalls() {
        assertThat(lifecycle.triggerResponse("s1", request("EXIT", 2, "stop")))
                .containsEntry("accepted", false).containsEntry("rejectReason", "STALE_PLAN_VERSION");
        assertThat(lifecycle.triggerResponse("s1", request("EXIT", 1.5, "stop")))
                .containsEntry("accepted", false).containsEntry("rejectReason", "BROKER_QUANTITY_INVALID");
        verifyNoInteractions(kis);
    }

    @Test
    void pendingUnknownIsNotReportedAsAcceptedOrResubmitted() {
        TradeSignalExecution intent = execution();
        intent.setStatus("UNKNOWN");
        intent.setOrderId(null);
        when(executions.findByUserIdAndStatusIn("u1", PaperTradeStore.UNRESOLVED)).thenReturn(List.of(intent));
        when(executions.findBySignalId("s1")).thenReturn(List.of(intent));
        assertThat(lifecycle.triggerResponse("s1", request("EXIT", 1, "stop"))).containsEntry("accepted", false)
                .containsEntry("executionStatus", "UNKNOWN").containsEntry("rejectReason", "ORDER_RECONCILIATION_REQUIRED");
        verify(kis, never()).paperOrder(anyString(), any(), anyString(), anyString(), anyInt(), anyLong(), anyString());
    }

    @Test
    void restartIntentWithoutBrokerIdentityBecomesUnknownWithoutGuessingOrReordering() {
        TradeSignalExecution intent = execution();
        intent.setOrderId(null);
        intent.setSubmittedAt(now.minusSeconds(31));
        when(executions.findByUserIdAndStatusIn("u1", PaperTradeStore.UNRESOLVED)).thenReturn(List.of(intent));
        lifecycle.reconcileAccount("u1", null);
        verify(store).markUnknown("e1");
        verifyNoInteractions(kis);
    }

    @Test
    void plannedExitRemainsAvailableAfterEntryExpiration() {
        signal.setPlannedExitAt(now.minusSeconds(1));
        TradeSignalExecution intent = execution();
        when(store.claim(eq("s1"), eq(1), eq(TradeConditions.TriggerType.EXIT), eq("planned-exit"), anyMap(),
                anyLong(), anyLong(), anyLong(), isNull(), eq(now))).thenReturn(intent);
        when(kis.paperOrder(anyString(), any(), anyString(), anyString(), anyInt(), anyLong(), anyString()))
                .thenReturn(Map.of("success", true));
        lifecycle.trigger("s1", request("EXIT", 1, "planned-exit"));
        verify(store).acknowledge(any(), anyMap());
        verify(store, never()).block(anyString(), anyString());
    }

    @Test
    void entryTtlCancelsOnlyWithAtomicCancellationOwnership() {
        TradeSignalExecution intent = execution();
        intent.setStatus("ORDER_SUBMITTED");
        intent.setOrderSide("BUY");
        intent.setSubmittedAt(now.minusMinutes(2));
        intent.setOrderExpiresAt(now.plusMinutes(3));
        when(executions.findByUserIdAndStatusIn("u1", PaperTradeStore.UNRESOLVED)).thenReturn(List.of(intent));
        when(kis.paperOrders(anyString(), any(), anyString(), any(), any())).thenReturn(List.of(Map.ofEntries(
                Map.entry("odno", "order1"), Map.entry("ord_dt", "20260904"), Map.entry("pdno", "005930"),
                Map.entry("sll_buy_dvsn_cd", "02"), Map.entry("ord_qty", "10"), Map.entry("tot_ccld_qty", "2"),
                Map.entry("rmn_qty", "8"), Map.entry("cnc_cfrm_qty", "0"), Map.entry("rjct_qty", "0"),
                Map.entry("cncl_yn", "N"), Map.entry("avg_prvs", "100"), Map.entry("ord_gno_brno", "org"))));
        when(store.markCancelRequested("e1")).thenReturn(true, false);
        when(kis.cancelPaperOrder(anyString(), any(), anyString(), anyString(), anyString(), anyInt()))
                .thenReturn(Map.of("success", true));
        lifecycle.reconcileAccount("u1", null);
        lifecycle.reconcileAccount("u1", null);
        verify(kis, times(1)).cancelPaperOrder("u1", user.getSecret(), "token", "order1", "org", 8);
        verify(store, times(2)).observeFill("e1", 2, 100, 8, false, "org", now);
    }

    @Test
    void resentProtectiveTriggerKeepsTheWorkingSellOrder() {
        TradeSignalExecution working = execution();
        working.setStatus("ORDER_SUBMITTED");
        working.setOrderExpiresAt(now.plusMinutes(2));
        when(executions.findByUserIdAndStatusIn("u1", PaperTradeStore.UNRESOLVED)).thenReturn(List.of(working));
        when(executions.findBySignalId("s1")).thenReturn(List.of(working));
        when(kis.paperOrders(anyString(), any(), anyString(), any(), any())).thenReturn(List.of(Map.ofEntries(
                Map.entry("odno", "order1"), Map.entry("ord_dt", "20260904"), Map.entry("pdno", "005930"),
                Map.entry("sll_buy_dvsn_cd", "01"), Map.entry("ord_qty", "10"), Map.entry("tot_ccld_qty", "0"),
                Map.entry("rmn_qty", "10"), Map.entry("cnc_cfrm_qty", "0"), Map.entry("rjct_qty", "0"),
                Map.entry("cncl_yn", "N"), Map.entry("avg_prvs", "0"), Map.entry("ord_gno_brno", "org"))));
        Map<String, Object> response = lifecycle.triggerResponse("s1", request("EXIT", 1, "stop"));
        assertThat(response).containsEntry("accepted", true).containsEntry("deduplicated", true)
                .containsEntry("executionStatus", "ORDER_SUBMITTED");
        verify(store, never()).markCancelRequested(anyString());
        verify(kis, never()).cancelPaperOrder(anyString(), any(), anyString(), anyString(), anyString(), anyInt());
        verify(kis, never()).paperOrder(anyString(), any(), anyString(), anyString(), anyInt(), anyLong(), anyString());
    }

    @Test
    void aStopCancelsAWorkingReductionSoTheFullExitCanFollow() {
        TradeSignalExecution reduction = workingSell("s1:1:REDUCE:trim:abc:0", 5);
        when(store.markCancelRequested("e1")).thenReturn(true);
        when(kis.cancelPaperOrder(anyString(), any(), anyString(), anyString(), anyString(), anyInt()))
                .thenReturn(Map.of("success", true));
        assertThat(lifecycle.triggerResponse("s1", request("EXIT", 1, "stop"))).containsEntry("accepted", false)
                .containsEntry("rejectReason", "ORDER_RECONCILIATION_REQUIRED");
        verify(kis).cancelPaperOrder("u1", user.getSecret(), "token", reduction.getOrderId(), "org", 5);
        verify(kis, never()).paperOrder(anyString(), any(), anyString(), anyString(), anyInt(), anyLong(), anyString());
    }

    @Test
    void aStopCancelsAnotherExitGroupsSellThatThePriceHasLeft() {
        workingSell("s1:1:EXIT:take-profit:0", 10);
        when(store.markCancelRequested("e1")).thenReturn(true);
        when(kis.cancelPaperOrder(anyString(), any(), anyString(), anyString(), anyString(), anyInt()))
                .thenReturn(Map.of("success", true));
        assertThat(lifecycle.triggerResponse("s1", request("EXIT", 1, "stop"))).containsEntry("accepted", false)
                .containsEntry("rejectReason", "ORDER_RECONCILIATION_REQUIRED");
        verify(kis).cancelPaperOrder("u1", user.getSecret(), "token", "order1", "org", 10);
    }

    @Test
    void aReductionLeavesAWorkingExitSellAlone() throws Exception {
        signal.setConditionPayload(mapper.writeValueAsString(Map.of("schema_version", 2,
                "exit_conditions", List.of(Map.of("id", "stop", "all", List.of(Map.of("field", "pnl_rate", "operator", "<=", "value", -5)))),
                "reduce_conditions", List.of(Map.of("id", "trim", "reduce_fraction", 0.5,
                        "all", List.of(Map.of("field", "pnl_rate", "operator", "<=", "value", -5)))))));
        workingSell("s1:1:EXIT:stop:0", 10);
        assertThat(lifecycle.triggerResponse("s1", request("REDUCE", 1, "trim"))).containsEntry("accepted", false)
                .containsEntry("rejectReason", "ORDER_RECONCILIATION_REQUIRED");
        verify(store, never()).markCancelRequested(anyString());
        verify(kis, never()).cancelPaperOrder(anyString(), any(), anyString(), anyString(), anyString(), anyInt());
        verify(kis, never()).paperOrder(anyString(), any(), anyString(), anyString(), anyInt(), anyLong(), anyString());
    }

    @Test
    void protectiveTriggerStillCancelsAWorkingEntryBuy() {
        TradeSignalExecution entry = execution();
        entry.setOrderSide("BUY");
        entry.setTriggerKey("s1:1:ENTRY:breakout:0");
        entry.setStatus("PARTIALLY_FILLED");
        entry.setOrderExpiresAt(now.plusMinutes(3));
        when(executions.findByUserIdAndStatusIn("u1", PaperTradeStore.UNRESOLVED)).thenReturn(List.of(entry));
        when(kis.paperOrders(anyString(), any(), anyString(), any(), any())).thenReturn(List.of(Map.ofEntries(
                Map.entry("odno", "order1"), Map.entry("ord_dt", "20260904"), Map.entry("pdno", "005930"),
                Map.entry("sll_buy_dvsn_cd", "02"), Map.entry("ord_qty", "10"), Map.entry("tot_ccld_qty", "4"),
                Map.entry("rmn_qty", "6"), Map.entry("cnc_cfrm_qty", "0"), Map.entry("rjct_qty", "0"),
                Map.entry("cncl_yn", "N"), Map.entry("avg_prvs", "100"), Map.entry("ord_gno_brno", "org"))));
        when(store.markCancelRequested("e1")).thenReturn(true);
        when(kis.cancelPaperOrder(anyString(), any(), anyString(), anyString(), anyString(), anyInt()))
                .thenReturn(Map.of("success", true));
        lifecycle.trigger("s1", request("EXIT", 1, "stop"));
        verify(kis).cancelPaperOrder("u1", user.getSecret(), "token", "order1", "org", 6);
    }

    @Test
    void autoTradeOffRefusesEntriesButStillSendsProtectiveExits() {
        user.setAutoTradeEnabled(false);
        TradeSignalExecution intent = execution();
        when(store.claim(eq("s1"), eq(1), eq(TradeConditions.TriggerType.EXIT), eq("stop"), anyMap(), eq(90L),
                eq(0L), eq(0L), isNull(), eq(now))).thenReturn(intent);
        when(kis.paperOrder(anyString(), any(), anyString(), anyString(), anyInt(), anyLong(), anyString()))
                .thenReturn(Map.of("success", true));
        Map<String, Object> snapshot = PaperTradeStoreTest.account(10);
        snapshot.put("holdings", List.of(Map.of("stockCode", "005930", "quantity", 10, "avgPrice", 100.0,
                "pnlRate", -10.0, "sellableQuantity", 10)));
        when(accounts.snapshot("u1")).thenReturn(snapshot);
        lifecycle.trigger("s1", request("EXIT", 1, "stop"));
        verify(kis).paperOrder(anyString(), any(), anyString(), anyString(), anyInt(), anyLong(), eq("SELL"));
        signal.setStatus("WAITING_ENTRY");
        assertThat(lifecycle.triggerResponse("s1", request("ENTRY", 1, "entry")))
                .containsEntry("accepted", false).containsEntry("rejectReason", "AUTO_TRADE_DISABLED");
        user.setAutoTradeEnabled(true);
    }

    @Test
    void oneUnreadableStoredPlanDoesNotFailTheActivePage() {
        TradeSignal legacy = new TradeSignal();
        ReflectionTestUtils.setField(legacy, "id", "legacy");
        legacy.setUserId("u1");
        legacy.setStockCode("000660");
        legacy.setStatus("OPEN");
        legacy.setConditionPayload(null);
        when(signals.findByStatusIn(eq(PaperTradeStore.ACTIVE), any(org.springframework.data.domain.Pageable.class)))
                .thenReturn(new org.springframework.data.domain.PageImpl<>(List.of(signal, legacy)));
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> rows = (List<Map<String, Object>>) lifecycle.active(0, 200).get("signals");
        assertThat(rows).hasSize(2);
        assertThat(rows.get(0).get("conditionPayload")).isNotNull();
        assertThat(rows.get(1)).containsEntry("conditionPayload", null).containsEntry("rejectReason", "INVALID_STORED_CONDITIONS");
    }

    @Test
    void activeRowsListEachPlansUnresolvedOrdersSoTheMonitorCanReportBlockedProtection() {
        TradeSignalExecution unknown = execution();
        unknown.setStatus("UNKNOWN");
        unknown.setOrderId(null);
        TradeSignalExecution other = execution();
        other.setSignalId("someone-else");
        other.setStatus("ORDER_SUBMITTED");
        other.setOrderId("0000123");
        when(executions.findByStatusInOrderBySubmittedAtAsc(PaperTradeStore.UNRESOLVED)).thenReturn(List.of(unknown, other));
        when(signals.findByStatusIn(eq(PaperTradeStore.ACTIVE), any(org.springframework.data.domain.Pageable.class)))
                .thenReturn(new org.springframework.data.domain.PageImpl<>(List.of(signal)));
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> rows = (List<Map<String, Object>>) lifecycle.active(0, 200).get("signals");
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> orders = (List<Map<String, Object>>) rows.get(0).get("unresolvedOrders");
        assertThat(orders).hasSize(1);
        assertThat(orders.get(0)).containsEntry("status", "UNKNOWN").containsEntry("orderSide", "SELL")
                .containsEntry("brokerOrderKnown", false);
    }

    private TradeSignalExecution unknownSell() {
        TradeSignalExecution unknown = execution();
        unknown.setStatus("UNKNOWN");
        unknown.setOrderId(null);
        unknown.setRejectReason("ORDER_ACCEPTANCE_UNKNOWN");
        when(executions.findById("e1")).thenReturn(Optional.of(unknown));
        return unknown;
    }

    private static Map<String, Object> brokerOrder(String odno, String stock, String time) {
        return Map.of("odno", odno, "ord_dt", "20260904", "pdno", stock, "sll_buy_dvsn_cd", "01", "ord_qty", "10",
                "ord_tmd", time, "ord_gno_brno", "06010");
    }

    @Test
    void operatorAdoptsOnlyABrokerOrderThatMatchesTheUnknownSubmission() {
        unknownSell();
        when(kis.paperOrders(eq("u1"), any(), eq("token"), any(), any())).thenReturn(List.of(
                brokerOrder("0000117057", "005930", "100001"), brokerOrder("0000117058", "000660", "100002")));
        assertThatThrownBy(() -> lifecycle.resolveUnknownOrder("e1", "117058", false, "KIS 앱 주문내역 확인"))
                .hasMessage("BROKER_ORDER_NOT_FOUND_FOR_THIS_SUBMISSION");     // another stock's order
        TradeSignalExecution other = execution();
        ReflectionTestUtils.setField(other, "id", "e9");
        when(executions.findByUserIdAndOrderId("u1", "0000117057")).thenReturn(List.of(other));
        assertThatThrownBy(() -> lifecycle.resolveUnknownOrder("e1", "117057", false, "KIS 앱 주문내역 확인"))
                .hasMessage("BROKER_ORDER_ALREADY_ASSOCIATED");
        verify(store, never()).adoptBrokerOrder(anyString(), anyString(), any(), anyString(), any());
        when(executions.findByUserIdAndOrderId("u1", "0000117057")).thenReturn(List.of());
        lifecycle.resolveUnknownOrder("e1", "117057", false, " KIS 앱 주문내역 확인 ");
        verify(store).adoptBrokerOrder("e1", "0000117057", "06010", "KIS 앱 주문내역 확인", now);
    }

    @Test
    void notSubmittedIsRefusedWhileTheBrokerListsAPossibleOrder() {
        unknownSell();
        when(kis.paperOrders(eq("u1"), any(), eq("token"), any(), any())).thenReturn(List.of(
                brokerOrder("0000117001", "005930", "093000"), brokerOrder("0000117057", "005930", "100001")));
        assertThatThrownBy(() -> lifecycle.resolveUnknownOrder("e1", null, true, "주문내역에 없음"))
                .hasMessage("BROKER_LISTS_POSSIBLE_ORDER:0000117057");
        verify(store, never()).confirmNotSubmitted(anyString(), anyString(), any());
        when(kis.paperOrders(eq("u1"), any(), eq("token"), any(), any())).thenReturn(List.of(
                brokerOrder("0000117001", "005930", "093000")));            // earlier than the submission
        lifecycle.resolveUnknownOrder("e1", null, true, "주문내역에 없음");
        verify(store).confirmNotSubmitted("e1", "주문내역에 없음", now);
    }

    @Test
    void onlyAnUnknownOrderWithoutBrokerIdCanBeResolvedAndANoteIsRequired() {
        TradeSignalExecution unknown = unknownSell();
        assertThatThrownBy(() -> lifecycle.resolveUnknownOrder("e1", "1", false, " ")).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> lifecycle.resolveUnknownOrder("e1", "1", true, "n")).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> lifecycle.resolveUnknownOrder("e1", null, false, "n")).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> lifecycle.resolveUnknownOrder("e1", "12a", false, "n")).isInstanceOf(IllegalArgumentException.class);
        unknown.setStatus("ORDER_SUBMITTED");
        assertThatThrownBy(() -> lifecycle.resolveUnknownOrder("e1", "1", false, "n")).hasMessage("EXECUTION_NOT_AWAITING_OPERATOR");
        verify(kis, never()).paperOrders(anyString(), any(), anyString(), any(), any());
    }

    @Test
    void operatorListShowsOnlyUnknownOrdersWithoutBrokerId() {
        TradeSignalExecution unknown = unknownSell();
        TradeSignalExecution known = execution();
        known.setStatus("UNKNOWN");
        when(executions.findByStatusInOrderBySubmittedAtAsc(List.of("UNKNOWN"))).thenReturn(List.of(unknown, known));
        assertThat(lifecycle.ordersAwaitingOperator()).singleElement().satisfies(row -> assertThat(row)
                .containsEntry("executionId", "e1").containsEntry("stockCode", "005930").containsEntry("orderSide", "SELL")
                .containsEntry("quantity", 10).containsEntry("rejectReason", "ORDER_ACCEPTANCE_UNKNOWN"));
    }

    private TradeSignalExecution execution() {
        TradeSignalExecution intent = new TradeSignalExecution();
        ReflectionTestUtils.setField(intent, "id", "e1");
        intent.setSignalId("s1");
        intent.setUserId("u1");
        intent.setStockCode("005930");
        intent.setTriggerKey("s1:1:EXIT:stop:0");
        intent.setOrderSide("SELL");
        intent.setSubmittedQuantity(10);
        intent.setSubmittedAt(now);
        intent.setOrderId("order1");
        intent.setAccountBinding("binding");
        intent.setStatus("INTENT");
        return intent;
    }
    /** A SELL still working at the broker for this plan, with nothing filled yet. */
    private TradeSignalExecution workingSell(String triggerKey, int quantity) {
        TradeSignalExecution working = execution();
        working.setTriggerKey(triggerKey);
        working.setSubmittedQuantity(quantity);
        working.setStatus("ORDER_SUBMITTED");
        working.setOrderExpiresAt(now.plusMinutes(2));
        when(executions.findByUserIdAndStatusIn("u1", PaperTradeStore.UNRESOLVED)).thenReturn(List.of(working));
        when(executions.findBySignalId("s1")).thenReturn(List.of(working));
        when(kis.paperOrders(anyString(), any(), anyString(), any(), any())).thenReturn(List.of(Map.ofEntries(
                Map.entry("odno", "order1"), Map.entry("ord_dt", "20260904"), Map.entry("pdno", "005930"),
                Map.entry("sll_buy_dvsn_cd", "01"), Map.entry("ord_qty", String.valueOf(quantity)), Map.entry("tot_ccld_qty", "0"),
                Map.entry("rmn_qty", String.valueOf(quantity)), Map.entry("cnc_cfrm_qty", "0"), Map.entry("rjct_qty", "0"),
                Map.entry("cncl_yn", "N"), Map.entry("avg_prvs", "0"), Map.entry("ord_gno_brno", "org"))));
        return working;
    }
    private Map<String, Object> request(String type, Number version, String group) {
        return Map.of("triggerType", type, "planVersion", version, "groupId", group);
    }
}
