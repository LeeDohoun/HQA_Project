package com.hqa.backend.service;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.Mockito.*;
import static org.mockito.ArgumentMatchers.*;
import com.hqa.backend.entity.*;
import com.hqa.backend.repository.*;
import java.time.OffsetDateTime;
import java.util.*;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;

class HistoricalTradingSnapshotServiceTest {
    final TradeSignalRepository signals = mock(TradeSignalRepository.class);
    final TradeSignalExecutionRepository executions = mock(TradeSignalExecutionRepository.class);
    final HistoricalTradingSnapshotService service = new HistoricalTradingSnapshotService(signals, executions);

    @Test
    void activityUsesOnlyAuthenticatedAccountsPlansAndNeverSeedsData() {
        TradeSignal mine = plan();
        mine.setReason("account-specific rationale");
        when(signals.findTop100ByUserIdOrderByCreatedAtDesc("u1")).thenReturn(List.of(mine));
        assertThat(service.aiActivity("u1", 6).get("leaders").toString()).contains("account-specific rationale");
        verify(signals, never()).findTop100ByOrderByCreatedAtDesc();
        verify(signals, never()).save(any());
        assertThat((List<?>) service.aiActivity("u2", 6).get("leaders")).isEmpty();
        assertThatThrownBy(() -> service.aiActivity(null, 6)).isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void orderHistoryPreservesBuyPartialSellAndExitAsSeparateExecutions() {
        TradeSignal plan = plan();
        TradeSignalExecution buy = execution("buy", "BUY", 10, 10, "FILLED");
        TradeSignalExecution reduce = execution("reduce", "SELL", 5, 2, "CANCELLED");
        TradeSignalExecution exit = execution("exit", "SELL", 8, 0, "ORDER_SUBMITTED");
        when(executions.historyForUser(eq("u1"), eq(false), any(), any(), any())).thenReturn(List.of(exit, reduce, buy));
        when(signals.findAllById(any())).thenReturn(List.of(plan));
        var response = service.orders("u1", "20260908", 50);
        @SuppressWarnings("unchecked") var rows = (List<Map<String, Object>>) response.get("orders");
        assertThat(rows).hasSize(3);
        assertThat(rows.get(0)).containsEntry("id", "exit").containsEntry("side", "sell")
                .containsEntry("status", "ORDER_SUBMITTED").containsEntry("filledQuantity", 0);
        assertThat(rows.get(1)).containsEntry("quantity", 5).containsEntry("filledQuantity", 2).containsEntry("filledAmount", 200L);
        assertThat(rows.get(2)).containsEntry("side", "buy");
        verify(executions).historyForUser(eq("u1"), eq(false),
                eq(OffsetDateTime.parse("2026-09-08T00:00:00+09:00")),
                eq(OffsetDateTime.parse("2026-09-09T00:00:00+09:00")), any());
    }

    @Test
    void emptyHistoryDoesNotInventOrdersFromUnexecutedPlans() {
        when(executions.historyForUser(eq("u1"), eq(true), any(), any(), any())).thenReturn(List.of());
        assertThat(service.orders("u1", null, 50)).containsEntry("count", 0);
        assertThatThrownBy(() -> service.orders("u1", "20260230", 50)).hasMessageContaining("date must");
        assertThatThrownBy(() -> service.orders(null, null, 50)).isInstanceOf(IllegalArgumentException.class);
    }

    private TradeSignal plan() {
        var s = new TradeSignal();
        ReflectionTestUtils.setField(s, "id", "s1");
        s.setUserId("u1"); s.setStockCode("005930"); s.setStockName("삼성전자"); s.setAction("BUY"); s.setStatus("CLOSED");
        return s;
    }
    private TradeSignalExecution execution(String id, String side, int submitted, int filled, String status) {
        var e = new TradeSignalExecution();
        ReflectionTestUtils.setField(e, "id", id);
        e.setSignalId("s1"); e.setUserId("u1"); e.setStockCode("005930"); e.setOrderSide(side);
        e.setSubmittedQuantity(submitted); e.setFilledQuantity(filled); e.setStatus(status);
        e.setOrderPrice(100L); e.setAverageFillPrice(filled > 0 ? 100L : null);
        e.setSubmittedAt(OffsetDateTime.parse("2026-09-08T10:00:00+09:00"));
        return e;
    }
}
