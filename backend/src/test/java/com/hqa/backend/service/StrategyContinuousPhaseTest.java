package com.hqa.backend.service;

import static com.hqa.backend.service.StrategyTestFixture.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.hqa.backend.dto.StrategyBatchRequest;
import com.hqa.backend.entity.StrategyBatch;
import com.hqa.backend.entity.StrategyBatchOrder;
import com.hqa.backend.entity.StrategyBatchSizing;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/** CONT_SELL / CONT_BUY continuous-session phases: sides, the sell-first wait, cash sizing, live price, cancels. */
class StrategyContinuousPhaseTest {
    private final StrategyTestFixture f = new StrategyTestFixture();

    @BeforeEach
    void enable() {
        f.properties.setOrdersEnabled(true);
        when(f.kis.paperAuctionOrder(eq("u1"), any(), eq("token"), anyString(), anyInt(), anyString()))
                .thenAnswer(inv -> accepted(inv.<String>getArgument(5).equals("SELL") ? "11111" : "2" + inv.getArgument(3)));
        when(f.kis.inquireCurrentPrice(eq("u1"), any(), eq("token"), anyString())).thenReturn(70_500L);
    }

    private static StrategyBatchRequest cont(String phase, List<StrategyBatchRequest.Order> lines, Double rate) {
        StrategyBatchRequest base = request(false, phase, SESSION, lines);
        return new StrategyBatchRequest(base.strategyId(), base.userId(), base.decisionHash(), base.decisionDate(),
                base.sessionDate(), phase, base.idempotencyKey(), false, base.expectedNavKrw(), lines, rate);
    }

    private StrategyBatch accept(String phase, List<StrategyBatchRequest.Order> lines, Double rate) {
        Map<String, Object> response = f.service.create(cont(phase, lines, rate));
        assertThat(response).as(response.toString()).containsEntry("status", "QUEUED");
        return f.batchRows.get(UUID.fromString((String) response.get("batchId")));
    }

    private static Map<String, Object> row(String odno, String code, String side, int qty, int filled, int remaining,
            String average, String cancelled, int cancelledQty) {
        Map<String, Object> row = new HashMap<>();
        row.putAll(Map.of("odno", odno, "ord_dt", "20261102", "pdno", code, "sll_buy_dvsn_cd", side,
                "ord_qty", String.valueOf(qty), "tot_ccld_qty", String.valueOf(filled), "rmn_qty", String.valueOf(remaining),
                "avg_prvs", average, "cncl_yn", cancelled, "cncl_cfrm_qty", String.valueOf(cancelledQty)));
        row.putAll(Map.of("rjct_qty", "0", "ord_gno_brno", "06010"));
        return row;
    }

    @Test
    void sidesMustMatchThePhaseAndTheCostReserveIsValidated() {
        f.clock.set("2026-11-02T09:06:00+09:00");
        Map<String, Object> mixed = f.service.create(cont("CONT_SELL", List.of(sell(1, "005930", 1, 70_000), buy(2, "000660", 1, 100_000)), null));
        assertThat(mixed.get("reasons")).isEqualTo(List.of("PHASE_SIDE_MISMATCH"));
        assertThat(mixed.get("rejectedOrders")).isEqualTo(List.of(Map.of("seq", 2, "reason", "PHASE_SIDE_MISMATCH")));
        assertThat(f.service.create(cont("CONT_SELL", List.of(sell(1, "005930", 1, 70_000)), 0.003)).get("reasons"))
                .isEqualTo(List.of("INVALID_REQUEST")); // costReserveRate is for CONT_BUY only
        f.clock.set("2026-11-02T09:21:00+09:00");
        assertThat(f.service.create(cont("CONT_BUY", List.of(buy(1, "000660", 1, 100_000)), 0.05)).get("reasons"))
                .isEqualTo(List.of("INVALID_REQUEST"));
        assertThat(f.service.create(cont("CONT_BUY", List.of(sell(1, "005930", 1, 70_000)), null)).get("reasons"))
                .isEqualTo(List.of("PHASE_SIDE_MISMATCH"));
        StrategyBatch buys = accept("CONT_BUY", List.of(buy(1, "000660", 1, 100_000)), null);
        assertThat(buys.getCostReserveRate()).isEqualTo(0.003);
        assertThat(f.order(buys, "000660").getPlannedQuantity()).isEqualTo(1L);
    }

    @Test
    void buysWaitForTheSellCancelsToBeReconciledThenAreSizedOnceFromOrderableCash() {
        f.clock.set("2026-11-02T09:06:00+09:00");
        StrategyBatch sells = accept("CONT_SELL", List.of(sell(1, "005930", 10, 70_000)), null);
        f.executor.runOnce();
        StrategyBatchOrder sell = f.order(sells, "005930");
        assertThat(sell.getStatus()).isEqualTo("SUBMITTED");
        assertThat(sell.getRefPriceAtSubmit()).isEqualTo(70_500L); // live price before the order
        assertThat(sell.getRefPrice()).isEqualTo(70_000L);         // plan reference kept

        f.clock.set("2026-11-02T09:20:00+09:00");
        StrategyBatch buys = accept("CONT_BUY", List.of(buy(1, "0126Z0", 3, 51_000), buy(2, "000660", 2, 100_000)), null);
        f.executor.runOnce();
        assertThat(buys.getStatus()).isEqualTo("QUEUED"); // the sell remainder is not cancelled yet
        verify(f.kis, never()).paperOrderableCash(any(), any(), any(), any(), anyLong());

        when(f.kis.paperOrders(any(), any(), any(), any(), any())).thenReturn(List.of(row("11111", "005930", "01", 10, 5, 5, "70400", "N", 0)));
        when(f.kis.cancelPaperOrder(any(), any(), any(), any(), any(), anyInt())).thenReturn(Map.of("success", true, "response", Map.of("rt_cd", "0")));
        f.reconciler.runOnce(); // 09:20: cancel the CONT_SELL remainder
        verify(f.kis).cancelPaperOrder("u1", f.user.getSecret(), "token", "11111", "06010", 5);
        f.executor.runOnce();
        verify(f.kis, never()).paperOrderableCash(any(), any(), any(), any(), anyLong()); // no read after the cancel yet

        f.clock.set("2026-11-02T09:20:20+09:00");
        when(f.kis.paperOrders(any(), any(), any(), any(), any())).thenReturn(List.of(row("11111", "005930", "01", 10, 5, 0, "70400", "Y", 5)));
        f.reconciler.runOnce();
        assertThat(sell.getStatus()).isEqualTo("CANCELLED");
        assertThat(sell.getRejectReason()).isEqualTo("CANCELLED_AT_WINDOW_END");

        f.clock.set("2026-11-02T09:20:30+09:00");
        when(f.kis.paperOrderableCash(eq("u1"), any(), eq("token"), eq("0126Z0"), eq(51_000L)))
                .thenReturn(Map.of("cash", 300_000L, "raw", Map.of("nrcvb_buy_amt", "300000", "ord_psbl_cash", "310000")));
        f.executor.runOnce();
        // required = (3 x 51,000 + 2 x 100,000) x 1.003 = 354,059 > 300,000: floor(q x 300,000 / 354,059)
        assertThat(f.order(buys, "0126Z0").getQuantity()).isEqualTo(2);
        assertThat(f.order(buys, "000660").getQuantity()).isEqualTo(1);
        verify(f.kis).paperAuctionOrder("u1", f.user.getSecret(), "token", "0126Z0", 2, "BUY");
        verify(f.kis).paperAuctionOrder("u1", f.user.getSecret(), "token", "000660", 1, "BUY");
        assertThat(buys.getStatus()).isEqualTo("SUBMITTED");

        StrategyBatchSizing sizing = f.sizingRows.get(buys.getId());
        assertThat(sizing.getOrderableCashKrw()).isEqualTo(300_000L);
        assertThat(sizing.getRequiredKrw()).isEqualTo(354_059L);
        assertThat(sizing.isScaled()).isTrue();
        assertThat(sizing.getKisFields()).contains("ord_psbl_cash");
        @SuppressWarnings("unchecked")
        Map<String, Object> view = (Map<String, Object>) f.service.describe(buys.getId()).get("sizing");
        assertThat(view).containsEntry("orderableCashKrw", 300_000L).containsEntry("scaled", true);
        assertThat((List<?>) view.get("quantities")).hasSize(2);
        @SuppressWarnings("unchecked")
        Map<String, Object> first = ((List<Map<String, Object>>) view.get("quantities")).get(0);
        assertThat(first).containsEntry("plannedQuantity", 3).containsEntry("finalQuantity", 2);
        @SuppressWarnings("unchecked")
        Map<String, Object> order = ((List<Map<String, Object>>) f.service.describe(buys.getId()).get("orders")).get(0);
        assertThat(order).containsEntry("plannedQuantity", 3L).containsEntry("quantity", 2L).containsEntry("refPriceAtSubmit", 70_500L);

        f.executor.runOnce(); // sealed: no second cash read
        verify(f.kis, times(1)).paperOrderableCash(any(), any(), any(), any(), anyLong());
    }

    @Test
    void sizingNeverRaisesQuantitiesAndSkipsOrdersScaledToZero() {
        f.clock.set("2026-11-02T09:21:00+09:00");
        StrategyBatch ample = accept("CONT_BUY", List.of(buy(1, "0126Z0", 3, 51_000)), 0.0);
        when(f.kis.paperOrderableCash(any(), any(), any(), any(), anyLong())).thenReturn(Map.of("cash", 10_000_000L, "raw", Map.of()));
        f.executor.runOnce();
        assertThat(f.order(ample, "0126Z0").getQuantity()).isEqualTo(3); // ample cash: unchanged, never raised
        assertThat(f.sizingRows.get(ample.getId()).isScaled()).isFalse();
        assertThat(f.sizingRows.get(ample.getId()).getScaleFactor()).isEqualTo(1.0);

        StrategyTestFixture g = new StrategyTestFixture();
        g.properties.setOrdersEnabled(true);
        g.clock.set("2026-11-02T09:21:00+09:00");
        when(g.kis.fetchAccessToken(any(), any())).thenReturn("token");
        when(g.kis.paperAuctionOrder(any(), any(), any(), anyString(), anyInt(), anyString())).thenReturn(accepted("9"));
        when(g.kis.paperOrderableCash(any(), any(), any(), any(), anyLong())).thenReturn(Map.of("cash", 60_000L, "raw", Map.of()));
        Map<String, Object> response = g.service.create(new StrategyBatchRequest("hf001_f_both", "u1", HASH, DECISION, SESSION,
                "CONT_BUY", StrategyBatchService.idempotencyKey("hf001_f_both", HASH, SESSION.toString(), "CONT_BUY", "u1"),
                false, 1L, List.of(buy(1, "0126Z0", 1, 51_000), buy(2, "000660", 1, 100_000)), 0.0));
        StrategyBatch tight = g.batchRows.get(UUID.fromString((String) response.get("batchId")));
        g.executor.runOnce();
        // 60,000 / 151,000: both floor to 0
        assertThat(g.ordersOf(tight)).allSatisfy(order -> {
            assertThat(order.getStatus()).isEqualTo("SKIPPED");
            assertThat(order.getRejectReason()).isEqualTo("SCALED_TO_ZERO");
        });
        verify(g.kis, never()).paperAuctionOrder(any(), any(), any(), any(), anyInt(), any());
    }

    @Test
    void aSealedSizingIsAppliedAfterARestartBeforeAnyOrder() {
        f.clock.set("2026-11-02T09:21:00+09:00");
        StrategyBatch buys = accept("CONT_BUY", List.of(buy(1, "0126Z0", 3, 51_000)), null);
        // The process stopped after sealing the record and before applying it.
        f.sizingRows.put(buys.getId(), new StrategyBatchSizing(buys.getId(), 60_000L, f.now(), "{}", 153_459L, 0.003,
                0.39, true, "[{\"seq\":1,\"stockCode\":\"0126Z0\",\"refPrice\":51000,\"plannedQuantity\":3,\"finalQuantity\":1}]"));
        f.newExecutor().runOnce();
        verify(f.kis, never()).paperOrderableCash(any(), any(), any(), any(), anyLong());
        verify(f.kis).paperAuctionOrder("u1", f.user.getSecret(), "token", "0126Z0", 1, "BUY");
        assertThat(f.order(buys, "0126Z0").getPlannedQuantity()).isEqualTo(3L);
    }

    @Test
    void anUnreadableCashBalanceSendsNothing() {
        f.clock.set("2026-11-02T09:21:00+09:00");
        StrategyBatch buys = accept("CONT_BUY", List.of(buy(1, "0126Z0", 3, 51_000)), null);
        when(f.kis.paperOrderableCash(any(), any(), any(), any(), anyLong())).thenThrow(new IllegalStateException("KIS down"));
        f.executor.runOnce();
        verify(f.kis, never()).paperAuctionOrder(any(), any(), any(), any(), anyInt(), any());
        assertThat(f.sizingRows).isEmpty();
        f.clock.set("2026-11-02T10:00:00+09:00");
        f.executor.runOnce();
        assertThat(f.order(buys, "0126Z0").getRejectReason()).isEqualTo("PHASE_WINDOW_CLOSED");
    }

    @Test
    void overlappingWindowsStillSendBuysOnlyAfterTheSellsAreDone() {
        f.properties.setContSellWindow("09:05-09:30");
        f.clock.set("2026-11-02T09:06:00+09:00");
        StrategyBatch sells = accept("CONT_SELL", List.of(sell(1, "005930", 10, 70_000)), null);
        f.executor.runOnce();
        f.clock.set("2026-11-02T09:21:00+09:00");
        StrategyBatch buys = accept("CONT_BUY", List.of(buy(1, "0126Z0", 1, 51_000)), null);
        when(f.kis.paperOrderableCash(any(), any(), any(), any(), anyLong())).thenReturn(Map.of("cash", 10_000_000L, "raw", Map.of()));
        f.executor.runOnce();
        assertThat(buys.getStatus()).isEqualTo("QUEUED"); // the sell is still open inside its window
        when(f.kis.paperOrders(any(), any(), any(), any(), any())).thenReturn(List.of(row("11111", "005930", "01", 10, 10, 0, "70100", "N", 0)));
        f.reconciler.runOnce();
        assertThat(f.order(sells, "005930").getStatus()).isEqualTo("FILLED");
        f.executor.runOnce();
        assertThat(f.order(buys, "0126Z0").getStatus()).isEqualTo("SUBMITTED");
    }

    @Test
    void buyRemaindersAreCancelledAtTheBuyWindowEndAndAuctionsReadNoLivePrice() {
        f.clock.set("2026-11-02T09:21:00+09:00");
        StrategyBatch buys = accept("CONT_BUY", List.of(buy(1, "0126Z0", 3, 51_000)), null);
        when(f.kis.paperOrderableCash(any(), any(), any(), any(), anyLong())).thenReturn(Map.of("cash", 10_000_000L, "raw", Map.of()));
        f.executor.runOnce();
        when(f.kis.paperOrders(any(), any(), any(), any(), any())).thenReturn(List.of(row("20126Z0", "0126Z0", "02", 3, 1, 2, "51200", "N", 0)));
        when(f.kis.cancelPaperOrder(any(), any(), any(), any(), any(), anyInt())).thenReturn(Map.of("success", true, "response", Map.of("rt_cd", "0")));
        f.clock.set("2026-11-02T09:59:00+09:00");
        f.reconciler.runOnce();
        verify(f.kis, never()).cancelPaperOrder(any(), any(), any(), any(), any(), anyInt());
        f.clock.set("2026-11-02T10:00:00+09:00");
        f.reconciler.runOnce();
        verify(f.kis).cancelPaperOrder("u1", f.user.getSecret(), "token", "20126Z0", "06010", 2);

        StrategyTestFixture g = new StrategyTestFixture(); // OPEN auction batch at 08:35
        g.properties.setOrdersEnabled(true);
        when(g.kis.paperAuctionOrder(any(), any(), any(), anyString(), anyInt(), anyString())).thenReturn(accepted("1"));
        StrategyBatch open = g.queued(basket());
        g.executor.runOnce();
        verify(g.kis, never()).inquireCurrentPrice(any(), any(), any(), any());
        assertThat(g.ordersOf(open)).allSatisfy(order -> assertThat(order.getRefPriceAtSubmit()).isNull());
    }
}
