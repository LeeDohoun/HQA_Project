package com.hqa.backend.service;

import static com.hqa.backend.service.StrategyTestFixture.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.hqa.backend.entity.StrategyBatch;
import com.hqa.backend.entity.StrategyBatchOrder;
import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class StrategyBatchReconcilerTest {
    private final StrategyTestFixture f = new StrategyTestFixture();
    private StrategyBatch batch;

    @BeforeEach
    void submitBasket() {
        f.properties.setOrdersEnabled(true);
        batch = f.queued(basket());
        when(f.kis.paperAuctionOrder(any(), any(), any(), eq("005930"), anyInt(), any())).thenReturn(accepted("0000011111"));
        when(f.kis.paperAuctionOrder(any(), any(), any(), eq("0126Z0"), anyInt(), any())).thenReturn(accepted("0000022222"));
        f.executor.runOnce();
        assertThat(batch.getStatus()).isEqualTo("SUBMITTED");
    }

    private static Map<String, Object> row(String odno, String code, String side, int qty, int filled, int remaining,
            String average, String cancelled, int cancelledQty) {
        Map<String, Object> row = new HashMap<>();
        row.put("odno", odno);
        row.put("ord_dt", "20261102");
        row.put("pdno", code);
        row.put("sll_buy_dvsn_cd", side);
        row.put("ord_qty", String.valueOf(qty));
        row.put("tot_ccld_qty", String.valueOf(filled));
        row.put("rmn_qty", String.valueOf(remaining));
        row.put("avg_prvs", average);
        row.put("cncl_yn", cancelled);
        row.put("cncl_cfrm_qty", String.valueOf(cancelledQty));
        row.put("rjct_qty", "0");
        row.put("ord_gno_brno", "06010");
        return row;
    }

    @Test
    void brokerFillsUpdateQuantityAndAveragePriceAndCompleteTheBatch() {
        f.clock.set("2026-11-02T09:01:00+09:00");
        when(f.kis.paperOrders(eq("u1"), any(), eq("token"), eq(SESSION), eq(SESSION))).thenReturn(List.of(
                row("11111", "005930", "01", 10, 10, 0, "69500", "N", 0),
                row("22222", "0126Z0", "02", 3, 3, 0, "51510", "N", 0)));
        f.reconciler.runOnce();
        StrategyBatchOrder sell = f.order(batch, "005930");
        assertThat(sell.getStatus()).isEqualTo("FILLED");
        assertThat(sell.getFilledQuantity()).isEqualTo(10);
        assertThat(sell.getAverageFillPrice()).isEqualByComparingTo(new BigDecimal("69500"));
        assertThat(f.order(batch, "0126Z0").getStatus()).isEqualTo("FILLED");
        assertThat(batch.getStatus()).isEqualTo("COMPLETED");
        assertThat(batch.getCompletedAt()).isNotNull();
        verify(f.kis, never()).cancelPaperOrder(any(), any(), any(), any(), any(), anyInt());
        // sell 0.714% below ref (adverse), buy 1.0% above ref (adverse) -> both positive
        assertThat(StrategyStatusService.averageSlippagePct(f.ordersOf(batch))).isCloseTo(0.8571, within(0.0001));

        f.reconciler.runOnce(); // nothing left to track: no further broker reads
        verify(f.kis, times(1)).paperOrders(any(), any(), any(), any(), any());
    }

    @Test
    void openBatchRemainderIsCancelledOnlyAfterOpenCancelAfter() {
        f.clock.set("2026-11-02T09:02:00+09:00");
        when(f.kis.paperOrders(any(), any(), any(), any(), any())).thenReturn(List.of(
                row("11111", "005930", "01", 10, 10, 0, "70000", "N", 0),
                row("22222", "0126Z0", "02", 3, 1, 2, "51000", "N", 0)));
        f.reconciler.runOnce();
        assertThat(f.order(batch, "0126Z0").getStatus()).isEqualTo("PARTIALLY_FILLED");
        assertThat(batch.getStatus()).isEqualTo("PARTIAL");
        verify(f.kis, never()).cancelPaperOrder(any(), any(), any(), any(), any(), anyInt());

        f.clock.set("2026-11-02T09:05:00+09:00");
        when(f.kis.cancelPaperOrder(any(), any(), any(), any(), any(), anyInt()))
                .thenReturn(Map.of("success", true, "response", Map.of("rt_cd", "0")));
        f.reconciler.runOnce();
        verify(f.kis).cancelPaperOrder("u1", f.user.getSecret(), "token", "0000022222", "06010", 2);
        StrategyBatchOrder buy = f.order(batch, "0126Z0");
        assertThat(buy.getCancelRequestedAt()).isNotNull();
        assertThat(buy.getStatus()).isEqualTo("PARTIALLY_FILLED"); // a cancel request is not a confirmation
        assertThat(f.risk.evaluate("u1").cancelsToday()).isEqualTo(1);

        f.clock.set("2026-11-02T09:05:20+09:00");
        when(f.kis.paperOrders(any(), any(), any(), any(), any())).thenReturn(List.of(
                row("11111", "005930", "01", 10, 10, 0, "70000", "N", 0),
                row("22222", "0126Z0", "02", 3, 1, 0, "51000", "Y", 2)));
        f.reconciler.runOnce();
        assertThat(buy.getStatus()).isEqualTo("CANCELLED");
        assertThat(buy.getFilledQuantity()).isEqualTo(1);
        assertThat(buy.getRejectReason()).isEqualTo("CANCELLED_AFTER_OPEN");
        assertThat(batch.getStatus()).isEqualTo("PARTIAL");
        assertThat(batch.getCompletedAt()).isNotNull();
        verify(f.kis, times(1)).cancelPaperOrder(any(), any(), any(), any(), any(), anyInt());
    }

    @Test
    void unfilledOrdersExpireAfterTheCloseAndAnUnfilledBatchIsRejected() {
        f.clock.set("2026-11-02T15:41:00+09:00");
        when(f.kis.paperOrders(any(), any(), any(), any(), any())).thenReturn(List.of(
                row("11111", "005930", "01", 10, 0, 10, "0", "N", 0),
                row("22222", "0126Z0", "02", 3, 0, 3, "0", "N", 0)));
        f.reconciler.runOnce();
        assertThat(f.ordersOf(batch)).allSatisfy(order -> {
            assertThat(order.getStatus()).isEqualTo("CANCELLED");
            assertThat(order.getRejectReason()).isEqualTo("UNFILLED_AT_SESSION_CLOSE");
        });
        assertThat(batch.getStatus()).isEqualTo("REJECTED");
    }

    @Test
    void anOrderMissingFromTheBrokerListAfterTheCloseBecomesUnknown() {
        f.clock.set("2026-11-02T09:01:00+09:00");
        when(f.kis.paperOrders(any(), any(), any(), any(), any())).thenReturn(List.of(
                row("11111", "005930", "01", 10, 10, 0, "70000", "N", 0)));
        f.reconciler.runOnce();
        assertThat(f.order(batch, "0126Z0").getStatus()).isEqualTo("SUBMITTED"); // the list may lag during the day
        f.clock.set("2026-11-02T15:41:00+09:00");
        f.reconciler.runOnce();
        assertThat(f.order(batch, "0126Z0").getStatus()).isEqualTo("UNKNOWN");
        assertThat(f.order(batch, "0126Z0").getRejectReason()).isEqualTo("BROKER_ORDER_NOT_FOUND");
        assertThat(batch.getStatus()).isEqualTo("PARTIAL");
    }

    @Test
    void aBrokerRowForADifferentOrderIsNotApplied() {
        f.clock.set("2026-11-02T09:01:00+09:00");
        when(f.kis.paperOrders(any(), any(), any(), any(), any())).thenReturn(List.of(
                row("11111", "000660", "01", 10, 10, 0, "70000", "N", 0)));
        f.reconciler.runOnce();
        StrategyBatchOrder sell = f.order(batch, "005930");
        assertThat(sell.getStatus()).isEqualTo("UNKNOWN");
        assertThat(sell.getRejectReason()).isEqualTo("BROKER_ORDER_IDENTITY_MISMATCH");
        assertThat(sell.getFilledQuantity()).isZero();
    }

    @Test
    void nothingIsReadBeforeTheSessionOpensOrWhileTheAccountIsSendingABatch() {
        f.reconciler.runOnce(); // 08:35: the OPEN batch cannot have fills yet
        verify(f.kis, never()).paperOrders(any(), any(), any(), any(), any());
        assertThat(batch.getStatus()).isEqualTo("SUBMITTED");

        f.clock.set("2026-11-02T15:21:00+09:00");
        String id = (String) f.service.create(request(false, "CLOSE", SESSION, List.of(sell(1, "000660", 1, 100_000)))).get("batchId");
        StrategyBatch close = f.batchRows.get(java.util.UUID.fromString(id));
        close.setStatus("SUBMITTING"); // the CLOSE batch is being sent inside its window
        f.reconciler.runOnce();
        verify(f.kis, never()).paperOrders(any(), any(), any(), any(), any());

        close.setStatus("SUBMITTED");
        when(f.kis.paperOrders(any(), any(), any(), any(), any())).thenReturn(List.of());
        f.reconciler.runOnce();
        verify(f.kis, times(1)).paperOrders(any(), any(), any(), any(), any());
    }

    @Test
    void pastSessionsAreCleanedUpOnce() {
        // A QUEUED batch of the previous session never ran.
        f.clock.set("2026-10-30T15:21:00+09:00");
        StrategyBatch stale = f.service.create(request(false, "CLOSE", LocalDate.parse("2026-10-30"), LocalDate.parse("2026-10-29"),
                List.of(sell(1, "000660", 1, 100_000)))).get("batchId") instanceof String id ? f.batchRows.get(java.util.UUID.fromString(id)) : null;
        assertThat(stale).isNotNull();

        f.clock.set("2026-11-03T08:10:00+09:00"); // next day: today's submitted OPEN batch is now in the past
        when(f.kis.paperOrders(eq("u1"), any(), eq("token"), eq(SESSION), eq(SESSION))).thenReturn(List.of(
                row("11111", "005930", "01", 10, 10, 0, "70000", "N", 0)));
        f.reconciler.runOnce();
        assertThat(f.ordersOf(stale)).allSatisfy(order -> {
            assertThat(order.getStatus()).isEqualTo("SKIPPED");
            assertThat(order.getRejectReason()).isEqualTo("SESSION_PASSED");
        });
        assertThat(stale.getStatus()).isEqualTo("REJECTED");
        assertThat(f.order(batch, "005930").getStatus()).isEqualTo("FILLED");
        assertThat(f.order(batch, "0126Z0").getStatus()).isEqualTo("UNKNOWN");
        assertThat(f.order(batch, "0126Z0").getRejectReason()).isEqualTo("UNRESOLVED_AFTER_SESSION");
        assertThat(batch.getStatus()).isEqualTo("PARTIAL");
        verify(f.kis, never()).cancelPaperOrder(any(), any(), any(), any(), any(), anyInt());

        f.reconciler.runOnce(); // nothing stale is left
        verify(f.kis, times(1)).paperOrders(any(), any(), any(), any(), any());
    }

    @Test
    void pastOrdersBecomeUnknownWhenKisCannotBeRead() {
        f.clock.set("2026-11-03T08:10:00+09:00");
        when(f.kis.paperOrders(any(), any(), any(), any(), any())).thenThrow(new IllegalStateException("KIS down"));
        f.reconciler.runOnce();
        assertThat(f.ordersOf(batch)).allSatisfy(order -> {
            assertThat(order.getStatus()).isEqualTo("UNKNOWN");
            assertThat(order.getRejectReason()).isEqualTo("UNRESOLVED_AFTER_SESSION");
        });
        assertThat(batch.getStatus()).isEqualTo("PARTIAL");
    }
}
