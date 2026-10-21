package com.hqa.backend.service;

import static com.hqa.backend.service.StrategyTestFixture.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.hqa.backend.config.StrategyProperties;
import com.hqa.backend.dto.StrategyBatchRequest;
import com.hqa.backend.entity.StrategyAccount;
import com.hqa.backend.entity.StrategyBatch;
import com.hqa.backend.entity.StrategyBatchOrder;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.Test;

/** OP001 §5 cancel-ratio exemption, the read-only broker order view, and isolation from foreign orders/holdings. */
class StrategyExemptionAndForeignOrdersTest {
    private final StrategyTestFixture f = new StrategyTestFixture();

    private void pf001(boolean terminated) {
        StrategyProperties.Profile profile = new StrategyProperties.Profile();
        profile.setDailyLossBuyBlock(false);
        profile.setDrawdownBuyBlock(false);
        profile.setTerminationDrawdownPct(25.0);
        profile.setCancelRatioExemptBbExits(true);
        f.properties.getProfiles().put("pf001", profile);
        StrategyAccount account = new StrategyAccount("u1", "pf001", "paper", true, f.now());
        account.activate(OffsetDateTime.parse("2026-11-02T08:00:00+09:00"), 100_000_000L);
        if (terminated) account.terminate(f.now(), "TERMINATION_DRAWDOWN:25");
        f.accountRows.put("u1", account);
    }

    /** Today's earlier batch whose two orders both had cancels requested: the cancel ratio (1.0) exceeds 0.5. */
    private void cancelRatioExceeded() {
        StrategyBatch earlier = new StrategyBatch();
        earlier.setUserId("u1");
        earlier.setStrategyId("pf001");
        earlier.setSessionDate(SESSION);
        earlier.setPhase("OPEN");
        earlier.setStatus("PARTIAL");
        earlier.setIdempotencyKey("e".repeat(64));
        earlier.setCreatedAt(f.now());
        f.batchRows.put(earlier.getId(), earlier);
        for (int i = 1; i <= 2; i++) {
            StrategyBatchOrder order = new StrategyBatchOrder();
            order.setBatchId(earlier.getId());
            order.setSeq(i);
            order.setStockCode(i == 1 ? "035420" : "035720");
            order.setSide("SELL");
            order.setQuantity(1);
            order.setRefPrice(1);
            order.setOrderType("MARKET");
            order.setStatus("CANCELLED");
            order.setCancelRequestedAt(f.now());
            order.setUpdatedAt(f.now());
            f.orderRows.put(order.getId(), order);
        }
    }

    private static StrategyBatchRequest request(String strategy, String phase, boolean dryRun, String exemption,
            List<StrategyBatchRequest.Order> lines) {
        return new StrategyBatchRequest(strategy, "u1", HASH, DECISION, SESSION, phase,
                StrategyBatchService.idempotencyKey(strategy, HASH, SESSION.toString(), phase, "u1"), dryRun, 100_000_000L,
                lines, null, exemption);
    }

    private static List<StrategyBatchRequest.Order> exits() {
        return List.of(sell(1, "005930", 10, 70_000), sell(2, "000660", 5, 100_000));
    }

    @Test
    void aScheduledBbExitSkipsOnlyTheCancelRatioLimit() {
        pf001(false);
        cancelRatioExceeded();
        f.clock.set("2026-11-02T15:21:00+09:00");
        assertThat(f.service.create(request("pf001", "CLOSE", true, null, exits())).get("reasons"))
                .isEqualTo(List.of("CANCEL_RATIO_EXCEEDED"));
        Map<String, Object> accepted = f.service.create(request("pf001", "CLOSE", false, "BB_SCHEDULED_EXIT", exits()));
        assertThat(accepted).containsEntry("status", "QUEUED");
        UUID id = UUID.fromString((String) accepted.get("batchId"));
        assertThat(f.batchRows.get(id).getExemption()).isEqualTo("BB_SCHEDULED_EXIT");
        assertThat(f.service.describe(id)).containsEntry("exemption", "BB_SCHEDULED_EXIT");
        assertThat(f.risk.evaluate("u1").ordersToday()).isEqualTo(4); // exempt orders still count in the daily totals
    }

    @Test
    void theOtherLimitsStillApplyToExemptBatches() {
        pf001(true);
        cancelRatioExceeded();
        f.clock.set("2026-11-02T15:21:00+09:00");
        f.properties.setMaxOrdersPerDay(3);
        assertThat(f.service.create(request("pf001", "CLOSE", true, "TERMINATION_LIQUIDATION", exits())).get("reasons"))
                .isEqualTo(List.of("DAILY_ORDER_LIMIT_EXCEEDED"));
        f.properties.setMaxOrdersPerDay(1500);
        f.halt("GLOBAL");
        assertThat(f.service.create(request("pf001", "CLOSE", true, "TERMINATION_LIQUIDATION", exits())).get("reasons"))
                .isEqualTo(List.of("TRADING_HALTED"));
        f.haltRows.clear();
        when(f.snapshots.paperUser("u1")).thenThrow(new IllegalArgumentException("PAPER_ACCOUNT_REQUIRED"));
        assertThat(f.service.create(request("pf001", "CLOSE", true, "TERMINATION_LIQUIDATION", exits())).get("reasons"))
                .isEqualTo(List.of("PAPER_ACCOUNT_REQUIRED"));
    }

    @Test
    void terminationLiquidationNeedsATerminatedAccount() {
        pf001(false);
        cancelRatioExceeded();
        f.clock.set("2026-11-02T09:06:00+09:00");
        assertThat(f.service.create(request("pf001", "CONT_SELL", true, "TERMINATION_LIQUIDATION", exits())).get("reasons"))
                .isEqualTo(List.of("INVALID_EXEMPTION"));
        pf001(true);
        assertThat(f.service.create(request("pf001", "CONT_SELL", true, "TERMINATION_LIQUIDATION", exits())))
                .containsEntry("status", "VALIDATED");
    }

    @Test
    void invalidExemptionsAreRejected() {
        pf001(true);
        f.clock.set("2026-11-02T15:21:00+09:00");
        // buys in the batch
        assertThat(f.service.create(request("pf001", "CLOSE", true, "TERMINATION_LIQUIDATION",
                List.of(sell(1, "005930", 1, 70_000), buy(2, "000660", 1, 100_000)))).get("reasons"))
                .asList().contains("INVALID_EXEMPTION");
        // a BB exit outside the CLOSE phase
        f.clock.set("2026-11-02T09:06:00+09:00");
        assertThat(f.service.create(request("pf001", "CONT_SELL", true, "BB_SCHEDULED_EXIT", exits())).get("reasons"))
                .isEqualTo(List.of("INVALID_EXEMPTION"));
        // unknown value
        assertThat(f.service.create(request("pf001", "CONT_SELL", true, "MANUAL", exits())).get("reasons"))
                .isEqualTo(List.of("INVALID_EXEMPTION"));
        // a strategy whose profile does not allow BB exit exemptions
        f.accountRows.put("u1", new StrategyAccount("u1", "hf001_f_both", "paper", true, f.now()));
        f.clock.set("2026-11-02T15:21:00+09:00");
        assertThat(f.service.create(request("hf001_f_both", "CLOSE", true, "BB_SCHEDULED_EXIT", exits())).get("reasons"))
                .isEqualTo(List.of("INVALID_EXEMPTION"));
        assertThat(f.properties.profile("hf001_f_both").isCancelRatioExemptBbExits()).isFalse();
    }

    private static Map<String, Object> row(String odno, String code, String side, int qty, int filled, int remaining, String cancelled) {
        Map<String, Object> row = new HashMap<>(Map.of("odno", odno, "ord_dt", "20261102", "pdno", code, "sll_buy_dvsn_cd", side,
                "ord_qty", String.valueOf(qty), "tot_ccld_qty", String.valueOf(filled), "rmn_qty", String.valueOf(remaining),
                "avg_prvs", filled > 0 ? "100000" : "0", "cncl_yn", cancelled, "cncl_cfrm_qty", "0"));
        row.putAll(Map.of("rjct_qty", "0", "ord_gno_brno", "06010", "ord_tmd", "083501"));
        return row;
    }

    @Test
    void foreignHoldingsAndOrdersAreNeverTouchedAndRaiseNothing() {
        f.properties.setOrdersEnabled(true);
        // The account also holds a foreign 005930 position and has a foreign open 005930 order.
        f.snapshot.put("holdings", List.of(Map.of("stockCode", "005930", "quantity", 10L, "evalAmount", 700_000L),
                Map.of("stockCode", "000660", "quantity", 5L, "evalAmount", 500_000L)));
        StrategyBatch ours = f.queued(List.of(sell(1, "000660", 5, 100_000)));
        when(f.kis.paperAuctionOrder(any(), any(), any(), eq("000660"), eq(5), eq("SELL"))).thenReturn(accepted("0000012345"));
        f.executor.runOnce();
        verify(f.kis, times(1)).paperAuctionOrder(any(), any(), any(), any(), anyInt(), any());

        f.clock.set("2026-11-02T09:06:00+09:00"); // after the OPEN cancel time
        when(f.kis.paperOrders(any(), any(), any(), any(), any())).thenReturn(List.of(
                row("99999", "005930", "02", 10, 0, 10, "N"),          // foreign, open
                row("12345", "000660", "01", 5, 2, 3, "N")));         // ours, partially filled
        when(f.kis.cancelPaperOrder(any(), any(), any(), any(), any(), anyInt())).thenReturn(Map.of("success", true, "response", Map.of("rt_cd", "0")));
        f.reconciler.runOnce();
        f.reconciler.runOnce();
        verify(f.kis, times(1)).cancelPaperOrder("u1", f.user.getSecret(), "token", "0000012345", "06010", 3);
        verify(f.kis, never()).cancelPaperOrder(any(), any(), any(), eq("99999"), any(), anyInt());
        verify(f.kis, never()).paperAuctionOrder(any(), any(), any(), eq("005930"), anyInt(), any());
        assertThat(f.order(ours, "000660").getFilledQuantity()).isEqualTo(2);
        assertThat(f.orderRows).hasSize(1); // nothing was created for the foreign order

        StrategyRiskGuard.RiskState state = f.risk.evaluate("u1");
        assertThat(state.halted()).isFalse();
        // Only our own counts show: 1 order, 1 cancel (the foreign order and holding are not counted).
        assertThat(state.ordersToday()).isEqualTo(1);
        assertThat(state.cancelsToday()).isEqualTo(1);
        assertThat(state.reasons()).containsExactly("CANCEL_RATIO_EXCEEDED"); // informational; blocks no order
        assertThat(state.buyAllowed()).isTrue();
        assertThat(f.haltRows).isEmpty();

        // The read-only broker view shows both, links only ours, and counts the foreign open order.
        StrategyBrokerOrderService view = new StrategyBrokerOrderService(f.accounts, f.orders, f.batches, f.snapshots, f.kis, f.sessions);
        Map<String, Object> listed = view.brokerOrders("u1", LocalDate.parse("2026-11-02"));
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> rows = (List<Map<String, Object>>) listed.get("orders");
        assertThat(listed).containsEntry("date", "2026-11-02").containsEntry("openOrders", 2);
        assertThat(rows.get(0)).containsEntry("odno", "99999").containsEntry("stockCode", "005930").containsEntry("side", "BUY")
                .containsEntry("orderQty", 10L).containsEntry("filledQty", 0L).containsEntry("remainingQty", 10L)
                .containsEntry("cancelled", false).containsEntry("orderTime", "08:35:01").containsEntry("strategyBatchOrderId", null);
        assertThat(rows.get(1)).containsEntry("side", "SELL")
                .containsEntry("strategyBatchOrderId", f.order(ours, "000660").getId().toString());
        verify(f.kis, times(1)).cancelPaperOrder(any(), any(), any(), any(), any(), anyInt()); // the view changed nothing

        assertThatThrownBy(() -> view.brokerOrders("stranger", null)).hasMessage("STRATEGY_ACCOUNT_NOT_FOUND");
        assertThatThrownBy(() -> view.brokerOrders("u1", LocalDate.parse("2026-11-03"))).hasMessage("DATE_IN_FUTURE");
    }
}
