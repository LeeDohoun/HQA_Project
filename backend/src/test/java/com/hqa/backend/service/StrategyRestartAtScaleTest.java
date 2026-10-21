package com.hqa.backend.service;

import static com.hqa.backend.service.StrategyTestFixture.*;
import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

import com.hqa.backend.dto.StrategyBatchRequest;
import com.hqa.backend.entity.StrategyBatch;
import com.hqa.backend.entity.StrategyBatchOrder;
import java.time.OffsetDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.Test;

/**
 * pf001 activation condition: ~500 CONT_BUY orders at the KIS PAPER pace of 1 request per second, with a
 * process restart in the middle. No order is sent twice, UNKNOWN orders are never resent, and the batch
 * completes inside its window; reconciliation and an operator adoption then complete it.
 */
class StrategyRestartAtScaleTest {
    private static final int ORDERS = 500;
    private static final int CRASH_AT = 250;     // the process dies while this order is at the broker
    private static final int AMBIGUOUS_AT = 100; // KIS answers without a body (acceptance unknown)

    /** Process death during the broker call: not a RuntimeException, so nothing in the executor handles it. */
    static final class Crash extends Error {
        Crash() { super("simulated process stop"); }
    }

    @Test
    void fiveHundredBuysSurviveARestartWithoutDuplicates() {
        StrategyTestFixture f = new StrategyTestFixture();
        f.properties.setOrdersEnabled(true);
        f.properties.setMaxOrderPctOfEquity(10.0);
        f.clock.set("2026-11-02T09:20:00+09:00");
        List<StrategyBatchRequest.Order> lines = new ArrayList<>();
        for (int i = 1; i <= ORDERS; i++) lines.add(buy(i, String.format("%06d", i), 1, 100_000));
        StrategyBatchRequest request = new StrategyBatchRequest("hf001_f_both", "u1", HASH, DECISION, SESSION, "CONT_BUY",
                StrategyBatchService.idempotencyKey("hf001_f_both", HASH, SESSION.toString(), "CONT_BUY", "u1"), false,
                100_000_000L, lines, null);
        Map<String, Object> response = f.service.create(request);
        assertThat(response).as(response.toString()).containsEntry("status", "QUEUED");
        StrategyBatch batch = f.batchRows.get(UUID.fromString((String) response.get("batchId")));

        // Fake broker: every KIS request takes one second of the simulated clock.
        Map<String, Integer> brokerOrders = new LinkedHashMap<>(); // stock -> times received
        Map<String, String> brokerIds = new HashMap<>();
        int[] calls = {0};
        Runnable pace = () -> f.clock.now = f.clock.now.plusSeconds(1);
        when(f.kis.paperOrderableCash(any(), any(), any(), any(), anyLong())).thenAnswer(inv -> {
            pace.run();
            return Map.of("cash", 1_000_000_000L, "raw", Map.of("nrcvb_buy_amt", "1000000000"));
        });
        when(f.kis.inquireCurrentPrice(any(), any(), any(), anyString())).thenAnswer(inv -> {
            pace.run();
            return 100_100L;
        });
        when(f.kis.paperAuctionOrder(eq("u1"), any(), eq("token"), anyString(), eq(1), eq("BUY"))).thenAnswer(inv -> {
            pace.run();
            String code = inv.getArgument(3);
            brokerOrders.merge(code, 1, Integer::sum);
            String odno = String.format("%010d", 1_000 + brokerOrders.size());
            brokerIds.put(code, odno);
            calls[0]++;
            if (calls[0] == CRASH_AT) throw new Crash();
            if (calls[0] == AMBIGUOUS_AT) return Map.of("success", false, "error", "HTTP 502");
            return accepted(odno);
        });

        StrategyBatchExecutor executor = f.executor;
        OffsetDateTime end = OffsetDateTime.parse("2026-11-02T10:00:00+09:00");
        int restarts = 0;
        while (!"SUBMITTED".equals(batch.getStatus()) && f.now().isBefore(end)) {
            try {
                executor.runOnce();
            } catch (Crash crash) {
                restarts++;
                executor = f.newExecutor(); // a new process over the same database
            }
            f.clock.now = f.clock.now.plusSeconds(2); // executor poll
        }

        assertThat(restarts).isEqualTo(1);
        assertThat(batch.getStatus()).isEqualTo("SUBMITTED");
        assertThat(f.now()).isBefore(end);
        assertThat(brokerOrders).hasSize(ORDERS).allSatisfy((code, times) -> assertThat(times).as(code).isEqualTo(1));
        verify(f.kis, times(1)).paperOrderableCash(any(), any(), any(), any(), anyLong());
        List<StrategyBatchOrder> orders = f.ordersOf(batch);
        assertThat(orders.stream().filter(order -> "UNKNOWN".equals(order.getStatus())).map(StrategyBatchOrder::getStockCode))
                .containsExactlyInAnyOrder(String.format("%06d", AMBIGUOUS_AT), String.format("%06d", CRASH_AT));
        assertThat(orders.stream().filter(order -> "SUBMITTED".equals(order.getStatus()))).hasSize(ORDERS - 2);
        assertThat(orders).allSatisfy(order -> assertThat(order.getRefPriceAtSubmit()).isEqualTo(100_100L));

        // Further ticks never resend anything, including the two UNKNOWN orders.
        f.newExecutor().runOnce();
        assertThat(brokerOrders.values()).allMatch(times -> times == 1);

        // The broker filled everything it received; reconciliation and operator adoption complete the batch.
        f.clock.set("2026-11-02T10:00:30+09:00");
        List<Map<String, Object>> rows = new ArrayList<>();
        brokerIds.forEach((code, odno) -> {
            Map<String, Object> row = new HashMap<>(Map.of("odno", odno, "ord_dt", "20261102", "pdno", code,
                    "sll_buy_dvsn_cd", "02", "ord_qty", "1", "tot_ccld_qty", "1", "rmn_qty", "0", "avg_prvs", "100100",
                    "cncl_yn", "N", "cncl_cfrm_qty", "0"));
            row.putAll(Map.of("rjct_qty", "0", "ord_gno_brno", "06010", "ord_tmd", "092500"));
            rows.add(row);
        });
        when(f.kis.paperOrders(any(), any(), any(), any(), any())).thenReturn(rows);
        f.reconciler.runOnce();
        assertThat(orders.stream().filter(order -> "FILLED".equals(order.getStatus()))).hasSize(ORDERS - 2);
        for (StrategyBatchOrder unknown : orders.stream().filter(order -> "UNKNOWN".equals(order.getStatus())).toList()) {
            f.resolver.resolve(unknown.getId(), "adopt", brokerIds.get(unknown.getStockCode()), "restart drill");
        }
        f.reconciler.runOnce();
        assertThat(orders).allSatisfy(order -> assertThat(order.getStatus()).isEqualTo("FILLED"));
        assertThat(batch.getStatus()).isEqualTo("COMPLETED");
        assertThat(brokerOrders.values()).allMatch(times -> times == 1);
    }
}
