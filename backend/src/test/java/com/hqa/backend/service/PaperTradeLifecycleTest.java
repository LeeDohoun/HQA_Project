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
    private Map<String, Object> request(String type, Number version, String group) {
        return Map.of("triggerType", type, "planVersion", version, "groupId", group);
    }
}
